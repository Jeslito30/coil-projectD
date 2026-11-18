#!/usr/bin/env python3
"""Create or update Grafana Viewer accounts for Project D (faculty and other read-only users).

Idempotent: existing users keep their password; their organisation role is forced back to Viewer.
New users get a random password that is written ONLY to a local mode-600 file, never to stdout.

Input: a CSV file kept outside Git (see grafana/viewers.csv.example):
    login,email,name
    faculty-dorsu,dorsu.faculty@example.org,DOrSU faculty

Run on the server, over loopback (Grafana is published on 127.0.0.1:13000):
    GRAFANA_ADMIN_PASSWORD=... python3 grafana/viewer_accounts.py --users /etc/projectd/viewers.csv
The admin password can also be read from deployment/contabo/.env with --env-file.

Stdlib only. Uses the Grafana HTTP API: /api/users/lookup, /api/admin/users, /api/org/users/<id>.
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import re
import secrets
import string
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

LOGIN_RE = re.compile(r"^[A-Za-z0-9._@-]{3,64}$")


class GrafanaError(RuntimeError):
    pass


class Grafana:
    def __init__(self, url: str, user: str, password: str, timeout: float = 10.0):
        self.url = url.rstrip("/")
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}", "Content-Type": "application/json",
                        "Accept": "application/json"}
        self.timeout = timeout

    def call(self, method: str, path: str, body: dict | None = None, ok_404: bool = False):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.url + path, data=data, method=method, headers=self.headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            if ok_404 and exc.code == 404:
                return None
            detail = exc.read().decode(errors="replace")[:300]
            raise GrafanaError(f"{method} {path} -> HTTP {exc.code}: {detail}") from None
        except urllib.error.URLError as exc:
            raise GrafanaError(f"cannot reach Grafana at {self.url}: {exc.reason}") from None


def read_env_file(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def read_users(path: Path) -> list[dict[str, str]]:
    users, seen = [], set()
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(line for line in handle if line.strip() and not line.lstrip().startswith("#"))
        missing = {"login", "email", "name"} - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"{path}: missing column(s): {', '.join(sorted(missing))}")
        for number, row in enumerate(reader, start=2):
            login, email, name = (row["login"] or "").strip(), (row["email"] or "").strip(), (row["name"] or "").strip()
            if not LOGIN_RE.match(login):
                raise SystemExit(f"{path}:{number}: invalid login {login!r}")
            if "@" not in email:
                raise SystemExit(f"{path}:{number}: invalid email {email!r}")
            if login.lower() in seen:
                raise SystemExit(f"{path}:{number}: duplicate login {login!r}")
            seen.add(login.lower())
            users.append({"login": login, "email": email, "name": name or login})
    if not users:
        raise SystemExit(f"{path}: no users")
    return users


def new_password(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def ensure_viewer(api: Grafana, user: dict[str, str], dry_run: bool) -> tuple[str, str | None]:
    """Returns (action, generated_password_or_None)."""
    found = api.call("GET", "/api/users/lookup?loginOrEmail=" + urllib.parse.quote(user["login"]), ok_404=True)
    password = None
    if found is None:
        if dry_run:
            return "would create", None
        password = new_password()
        created = api.call("POST", "/api/admin/users", {
            "name": user["name"], "email": user["email"], "login": user["login"], "password": password})
        user_id, action = created["id"], "created"
    else:
        user_id, action = found["id"], "exists"
        if found.get("isGrafanaAdmin"):
            raise GrafanaError(f"{user['login']} is a Grafana server admin; refusing to downgrade it here")

    org_users = api.call("GET", "/api/org/users")
    entry = next((u for u in org_users if u.get("userId") == user_id), None)
    if entry is None:
        if dry_run:
            return action + ", would add to org as Viewer", None
        api.call("POST", "/api/org/users", {"loginOrEmail": user["login"], "role": "Viewer"})
        return action + ", added to org as Viewer", password
    if entry.get("role") != "Viewer":
        if dry_run:
            return f"{action}, would change role {entry.get('role')} -> Viewer", None
        api.call("PATCH", f"/api/org/users/{user_id}", {"role": "Viewer"})
        return f"{action}, role {entry.get('role')} -> Viewer", password
    return action + ", already Viewer", password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--users", type=Path, required=True, help="CSV with login,email,name (outside Git)")
    parser.add_argument("--url", default="http://127.0.0.1:13000", help="Grafana URL (default: loopback binding)")
    parser.add_argument("--env-file", type=Path, help="read GRAFANA_ADMIN_USER/PASSWORD from this .env file")
    parser.add_argument("--passwords-out", type=Path, default=Path("grafana-viewer-passwords.txt"),
                        help="where new passwords are written (mode 600; deliver privately, then delete)")
    parser.add_argument("--dry-run", action="store_true", help="show what would change; change nothing")
    args = parser.parse_args(argv)

    env = read_env_file(args.env_file) if args.env_file else {}
    admin_user = os.environ.get("GRAFANA_ADMIN_USER") or env.get("GRAFANA_ADMIN_USER") or "admin"
    admin_password = os.environ.get("GRAFANA_ADMIN_PASSWORD") or env.get("GRAFANA_ADMIN_PASSWORD")
    if not admin_password:
        print("Set GRAFANA_ADMIN_PASSWORD or pass --env-file deployment/contabo/.env", file=sys.stderr)
        return 2

    users = read_users(args.users)
    api = Grafana(args.url, admin_user, admin_password)
    new_passwords: list[str] = []
    failures = 0
    for user in users:
        try:
            action, password = ensure_viewer(api, user, args.dry_run)
        except GrafanaError as exc:
            print(f"FAIL  {user['login']}: {exc}")
            failures += 1
            continue
        print(f"OK    {user['login']}: {action}")
        if password:
            new_passwords.append(f"{user['login']}\t{user['email']}\t{password}")

    if new_passwords:
        old_umask = os.umask(0o077)
        try:
            with args.passwords_out.open("a", encoding="utf-8") as handle:
                handle.write("\n".join(new_passwords) + "\n")
        finally:
            os.umask(old_umask)
        os.chmod(args.passwords_out, 0o600)
        print(f"{len(new_passwords)} new password(s) appended to {args.passwords_out} (mode 600). "
              "Deliver them privately, ask users to change them at first login, then delete the file.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
