#!/usr/bin/env python3
"""Check Project D alert e-mail delivery in two independent steps.

  direct   Connect to the SMTP server with the GF_SMTP_* settings from deployment/contabo/.env and send
           one test message. Separates SMTP problems (host, port, TLS, credentials, sender policy)
           from Grafana problems.
  grafana  Ask the running Grafana to send a test notification through an e-mail receiver
           (Alertmanager receivers test API). Proves Grafana's own SMTP configuration works.

Run both on the server; Grafana is reached over its loopback binding:
    python3 grafana/smtp_test.py direct  --env-file deployment/contabo/.env
    python3 grafana/smtp_test.py grafana --env-file deployment/contabo/.env

Neither step proves that a real alert is routed: finish with the Alert 1 / Alert 2 tests and
record the received e-mails as evidence. Passwords are never printed.
"""
from __future__ import annotations

import argparse
import base64
import json
import smtplib
import socket
import ssl
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path


def read_env_file(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def split_host(host_port: str) -> tuple[str, int]:
    if not host_port:
        raise SystemExit("GF_SMTP_HOST is empty: set it to host:port, for example smtp.example.org:587")
    host, _, port = host_port.rpartition(":")
    if not host or not port.isdigit():
        raise SystemExit(f"GF_SMTP_HOST must be host:port, got {host_port!r}")
    return host, int(port)


def direct(cfg: dict[str, str], to: str, timeout: float) -> int:
    host, port = split_host(cfg.get("GF_SMTP_HOST", ""))
    sender = cfg.get("GF_SMTP_FROM_ADDRESS", "")
    user, password = cfg.get("GF_SMTP_USER", ""), cfg.get("GF_SMTP_PASSWORD", "")
    if not sender:
        raise SystemExit("GF_SMTP_FROM_ADDRESS is empty")
    if cfg.get("GF_SMTP_ENABLED", "false").lower() != "true":
        print("WARNING: GF_SMTP_ENABLED is not true; Grafana itself will not send e-mail until it is.")
    context = ssl.create_default_context()
    print(f"Connecting to {host}:{port} ({'implicit TLS' if port == 465 else 'STARTTLS'}) ...")
    try:
        if port == 465:
            client = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
        else:
            client = smtplib.SMTP(host, port, timeout=timeout)
            client.ehlo()
            if client.has_extn("starttls"):
                client.starttls(context=context)
                client.ehlo()
            elif user:
                print("FAIL  server does not offer STARTTLS; refusing to send credentials in plaintext")
                return 1
        with client:
            if user:
                client.login(user, password)
                print("PASS  authentication accepted")
            msg = EmailMessage()
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            msg["Subject"] = f"[Project D] SMTP test {stamp}"
            msg["From"], msg["To"] = sender, to
            msg.set_content(f"Direct SMTP test from grafana/smtp_test.py at {stamp}.\n"
                            "If you received this, the SMTP settings work outside Grafana.\n")
            refused = client.send_message(msg)
    except (smtplib.SMTPException, OSError, socket.timeout, ssl.SSLError) as exc:
        print(f"FAIL  {type(exc).__name__}: {exc}")
        return 1
    if refused:
        print(f"FAIL  recipients refused: {sorted(refused)}")
        return 1
    print(f"PASS  message accepted for {to} at {stamp} UTC; confirm it arrived (check spam) and keep the evidence")
    return 0


def grafana(cfg: dict[str, str], to: str, url: str, timeout: float) -> int:
    user = cfg.get("GRAFANA_ADMIN_USER") or "admin"
    password = cfg.get("GRAFANA_ADMIN_PASSWORD")
    if not password:
        raise SystemExit("GRAFANA_ADMIN_PASSWORD missing from the env file")
    body = {
        "receivers": [{
            "name": "project-d-smtp-test",
            "grafana_managed_receiver_configs": [{
                "name": "project-d-smtp-test", "type": "email",
                "settings": {"addresses": to, "singleEmail": True}, "disableResolveMessage": True,
            }],
        }],
        "alert": {"labels": {"alertname": "Project D SMTP test"},
                  "annotations": {"summary": "Test notification sent by grafana/smtp_test.py"}},
    }
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    request = urllib.request.Request(
        url.rstrip("/") + "/api/alertmanager/grafana/config/api/v1/receivers/test",
        data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        print(f"FAIL  Grafana answered HTTP {exc.code}: {detail}")
        return 1
    except urllib.error.URLError as exc:
        print(f"FAIL  cannot reach Grafana at {url}: {exc.reason}")
        return 1
    failures = 0
    for receiver in result.get("receivers", []):
        for config in receiver.get("grafana_managed_receiver_configs", []):
            status, error = config.get("status"), config.get("error")
            if status == "ok" and not error:
                print(f"PASS  Grafana sent the test notification to {to}")
            else:
                failures += 1
                print(f"FAIL  Grafana receiver status={status!r} error={error!r}")
    if not result.get("receivers"):
        print(f"FAIL  unexpected response: {json.dumps(result)[:300]}")
        return 1
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Project D alert e-mail checks")
    parser.add_argument("mode", choices=["direct", "grafana"])
    parser.add_argument("--env-file", type=Path, default=Path("deployment/contabo/.env"))
    parser.add_argument("--to", help="recipient (default: GRAFANA_ALERT_EMAIL from the env file)")
    parser.add_argument("--url", default="http://127.0.0.1:13000", help="Grafana URL for 'grafana' mode")
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args(argv)
    cfg = read_env_file(args.env_file)
    to = args.to or cfg.get("GRAFANA_ALERT_EMAIL", "")
    if "@" not in to:
        raise SystemExit("No recipient: set GRAFANA_ALERT_EMAIL in the env file or pass --to")
    if args.mode == "direct":
        return direct(cfg, to, args.timeout)
    return grafana(cfg, to, args.url, args.timeout)


if __name__ == "__main__":
    sys.exit(main())
