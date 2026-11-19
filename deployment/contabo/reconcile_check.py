#!/usr/bin/env python3
"""Read-only comparison of the RUNNING Project D containers with deployment/contabo/compose.yml.

Purpose: before the repository stack replaces a deployment that was started by hand or from another
compose file, show every difference that could lose data or expose a port:
  * a different Compose project name   -> "up" would create NEW, EMPTY named volumes
  * data paths on anonymous volumes    -> data is lost when the container is recreated
  * different images / tags            -> upgrade or downgrade on first "up"
  * ports published on 0.0.0.0 / ::    -> reachable from the internet, not only through WireGuard
  * missing 10.66.0.1, wg0 or DOCKER-USER jump

It changes nothing and never prints environment variables (they contain tokens and passwords).
Run on the server from the repository root, as a user that may run docker:
    sudo python3 deployment/contabo/reconcile_check.py > reconcile-$(date -u +%F).md
--env-file is optional: only the structure of compose.yml is compared, never values, so required
variables missing from the host's .env are filled with placeholders for rendering.
Exit status: 0 = no HIGH findings, 1 = at least one HIGH finding, 2 = could not run.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deployment" / "contabo" / "compose.yml"
SERVICES = ("mosquitto", "telegraf", "influxdb", "grafana")
# Container paths that hold data which must survive container recreation.
DATA_PATHS = {"influxdb": "/var/lib/influxdb2", "grafana": "/var/lib/grafana", "mosquitto": "/mosquitto/data"}
PUBLIC_HOSTS = {"", "0.0.0.0", "::", "[::]"}


@dataclass
class Report:
    findings: list[tuple[str, str]] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)

    def add(self, level: str, text: str) -> None:
        self.findings.append((level, text))

    def out(self, text: str = "") -> None:
        self.lines.append(text)


def run(cmd: list[str], check: bool = True) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])} ... failed: {result.stderr.strip()[:300]}")
    return result.stdout


def image_repo(image: str) -> str:
    """'docker.io/library/influxdb:2.7.12@sha256:..' -> 'influxdb'; 'grafana/grafana:12' -> 'grafana/grafana'."""
    image = image.split("@", 1)[0]
    last = image.rsplit("/", 1)[-1]
    if ":" in last:
        image = image[: len(image) - len(last)] + last.split(":", 1)[0]
    for prefix in ("docker.io/library/", "docker.io/", "library/"):
        if image.startswith(prefix):
            image = image[len(prefix):]
    return image


# Variables compose.yml marks as required (${VAR:?...}). Only the model's structure is compared,
# never values, so a placeholder lets the check run on a host whose .env was written for another stack.
REQUIRED_VARS = ("INFLUX_ADMIN_USER", "INFLUX_ADMIN_PASSWORD", "INFLUX_TOKEN", "MQTT_TELEGRAF_PASSWORD",
                 "GRAFANA_ADMIN_PASSWORD", "GRAFANA_ALERT_EMAIL")


def expected_model(env_file: Path | None) -> dict:
    cmd = ["docker", "compose", "-f", str(COMPOSE)]
    if env_file:
        cmd[2:2] = ["--env-file", str(env_file)]
    present = set()
    if env_file and env_file.exists():
        present = {line.split("=", 1)[0].strip() for line in env_file.read_text(encoding="utf-8").splitlines()
                   if "=" in line and not line.lstrip().startswith("#") and line.split("=", 1)[1].strip()}
    env = dict(os.environ)
    for name in REQUIRED_VARS:
        if name not in present and not env.get(name):
            env[name] = "reconcile-placeholder"
    result = subprocess.run(cmd + ["config", "--format", "json"], capture_output=True, text=True, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"docker compose config failed: {result.stderr.strip()[:300]}")
    return json.loads(result.stdout)


def expected_ports(service: dict) -> set[tuple[str, str, str]]:
    ports = set()
    for port in service.get("ports", []):
        ports.add((port.get("host_ip", "") or "0.0.0.0", str(port.get("published", "")), str(port.get("target"))))
    return ports


def running_ports(container: dict) -> set[tuple[str, str, str]]:
    ports = set()
    for key, bindings in (container.get("NetworkSettings", {}).get("Ports") or {}).items():
        target = key.split("/", 1)[0]
        for binding in bindings or []:
            ports.add((binding.get("HostIp") or "0.0.0.0", str(binding.get("HostPort")), target))
    return ports


def find_containers() -> list[dict]:
    ids = run(["docker", "ps", "-aq", "--no-trunc"]).split()
    return json.loads(run(["docker", "inspect", *ids])) if ids else []


def match_service(name: str, model_image: str, containers: list[dict]) -> list[dict]:
    by_label = [c for c in containers if (c["Config"].get("Labels") or {}).get("com.docker.compose.service") == name]
    project_labelled = [c for c in by_label if image_repo(c["Config"]["Image"]) == image_repo(model_image)]
    if project_labelled:
        return project_labelled
    return [c for c in containers if image_repo(c["Config"]["Image"]) == image_repo(model_image)]


def check_services(model: dict, containers: list[dict], report: Report) -> None:
    project = model.get("name", "")
    report.out("## Services")
    report.out()
    report.out("| Service | Running container | Compose project | Image (running → repo) | State |")
    report.out("| --- | --- | --- | --- | --- |")
    projects_seen = set()
    details = []
    for name in SERVICES:
        service = model["services"][name]
        matches = match_service(name, service["image"], containers)
        if not matches:
            report.out(f"| {name} | — | — | — → `{service['image']}` | not running |")
            report.add("INFO", f"{name}: no running container found; the repository stack would start it fresh.")
            continue
        if len(matches) > 1:
            report.add("MEDIUM", f"{name}: {len(matches)} candidate containers ({', '.join(c['Name'].lstrip('/') for c in matches)}); "
                                 "check for duplicates before switching stacks.")
        container = matches[0]
        labels = container["Config"].get("Labels") or {}
        running_project = labels.get("com.docker.compose.project", "(not compose)")
        projects_seen.add(running_project)
        running_image = container["Config"]["Image"]
        state = container["State"]["Status"]
        report.out(f"| {name} | `{container['Name'].lstrip('/')}` | `{running_project}` | `{running_image}` → `{service['image']}` | {state} |")
        if running_image.split("@")[0] != service["image"]:
            report.add("MEDIUM", f"{name}: running `{running_image}`, repository pins `{service['image']}`; "
                                 "the first `up` changes the version. Back up first and read the release notes.")
        details.append((name, service, container))

    if projects_seen and project not in projects_seen:
        report.add("HIGH", f"Running stack uses Compose project {sorted(projects_seen)}, the repository uses `{project}`. "
                           f"`docker compose up` from the repository would create new, EMPTY volumes named "
                           f"`{project}_<volume>` and a second set of containers that conflict on ports. "
                           "Migrate the volumes (see deployment/contabo/README.md, 'Adopting an existing deployment') "
                           "before switching.")

    report.out()
    report.out("## Data persistence")
    report.out()
    report.out("| Service | Data path | Running mount | Repository mount |")
    report.out("| --- | --- | --- | --- |")
    volumes = set(run(["docker", "volume", "ls", "-q"]).split())
    for name, service, container in details:
        path = DATA_PATHS.get(name)
        if not path:
            continue
        mount = next((m for m in container.get("Mounts", []) if m.get("Destination") == path), None)
        repo_volume = next((v for v in service.get("volumes", []) if v.get("target") == path), None)
        repo_desc = f"volume `{project}_{repo_volume['source']}`" if repo_volume and repo_volume.get("type") == "volume" else str(repo_volume)
        if mount is None:
            running_desc = "**none (data inside the container layer)**"
            report.add("HIGH", f"{name}: {path} is not on a volume; recreating the container deletes the data. "
                               "Copy it out with `docker cp` before any change.")
        elif mount["Type"] == "volume" and len(mount.get("Name", "")) == 64 and labels_anonymous(mount):
            running_desc = f"anonymous volume `{mount['Name'][:12]}…`"
            report.add("HIGH", f"{name}: {path} is on an anonymous volume; `docker compose up` from another project "
                               "will not reuse it. Copy it into the named volume before switching.")
        elif mount["Type"] == "volume":
            running_desc = f"volume `{mount['Name']}`"
            if repo_volume and mount["Name"] != f"{project}_{repo_volume['source']}":
                report.add("MEDIUM", f"{name}: data is in volume `{mount['Name']}`, the repository expects "
                                     f"`{project}_{repo_volume['source']}`" +
                                     (" (which already exists — check which one holds the real data)."
                                      if f"{project}_{repo_volume['source']}" in volumes else " (does not exist yet)."))
        else:
            running_desc = f"bind `{mount.get('Source')}`"
            report.add("MEDIUM", f"{name}: data is a bind mount `{mount.get('Source')}`; the repository uses a named volume.")
        report.out(f"| {name} | `{path}` | {running_desc} | {repo_desc} |")

    report.out()
    report.out("## Published ports")
    report.out()
    report.out("| Service | Running (host_ip:published→target) | Repository |")
    report.out("| --- | --- | --- |")
    for name, service, container in details:
        running, wanted = running_ports(container), expected_ports(service)
        fmt = lambda ports: ", ".join(f"{h}:{p}→{t}" for h, p, t in sorted(ports)) or "none"
        report.out(f"| {name} | {fmt(running)} | {fmt(wanted)} |")
        for host, published, target in running:
            if host in PUBLIC_HOSTS:
                report.add("HIGH", f"{name}: port {published}→{target} is published on all interfaces ({host}); "
                                   "it is reachable from the internet unless a provider firewall blocks it.")
        if running != wanted:
            report.add("INFO", f"{name}: published ports differ from the repository ({fmt(running)} vs {fmt(wanted)}).")
    if any(n == "influxdb" for n, _, _ in details):
        influx = next(c for n, _, c in details if n == "influxdb")
        if running_ports(influx):
            report.add("HIGH", "influxdb: InfluxDB is published on the host; the design keeps it on the private Docker network only.")


def labels_anonymous(mount: dict) -> bool:
    try:
        info = json.loads(run(["docker", "volume", "inspect", mount["Name"]]))[0]
    except (RuntimeError, IndexError, json.JSONDecodeError):
        return False
    return "com.docker.volume.anonymous" in (info.get("Labels") or {}) or not info.get("Labels")


def check_host(report: Report) -> None:
    report.out()
    report.out("## Host network")
    report.out()
    addresses = run(["ip", "-4", "-o", "addr", "show"], check=False) if shutil.which("ip") else ""
    has_vpn_ip = " 10.66.0.1/" in addresses
    report.out(f"- 10.66.0.1 configured: {'yes' if has_vpn_ip else 'NO'}")
    if not has_vpn_ip:
        report.add("HIGH", "10.66.0.1 is not configured; the repository stack cannot bind MQTT/Grafana to the VPN address.")
    if shutil.which("systemctl"):
        after = run(["systemctl", "show", "docker", "-p", "After"], check=False)
        ordered = "wg-quick@wg0.service" in after
        report.out(f"- Docker starts after wg-quick@wg0: {'yes' if ordered else 'NO'}")
        if not ordered:
            report.add("MEDIUM", "Docker is not ordered after wg-quick@wg0. After a reboot, containers bound to "
                                 "10.66.0.1 can fail to start and stay down. Install "
                                 "deployment/contabo/docker-after-wireguard.conf.")
    if shutil.which("wg"):
        peers = run(["wg", "show", "wg0", "peers"], check=False).split()
        report.out(f"- wg0 peers: {len(peers)}")
    else:
        report.out("- wg: not installed or not in PATH (run as root to read wg0)")
    if shutil.which("iptables"):
        rules = run(["iptables", "-S", "DOCKER-USER"], check=False).splitlines()
        appended = [r for r in rules if r.startswith("-A")]
        first = appended[0] if appended else "(empty or not readable — run as root)"
        report.out(f"- first DOCKER-USER rule: `{first}`")
        if appended and first != "-A DOCKER-USER -i wg0 -j PROJECTD-WG":
            report.add("MEDIUM", "DOCKER-USER does not start with the Project D jump; run "
                                 "`sudo vpn/firewall/docker-user.sh --check` and apply the reviewed rules.")
        returns = [i for i, r in enumerate(appended) if r == "-A DOCKER-USER -j RETURN"]
        drops = [i for i, r in enumerate(appended) if r.endswith("-j DROP")]
        if returns and any(d > returns[0] for d in drops):
            report.add("HIGH", "A DROP rule sits after `-j RETURN` in DOCKER-USER and is never evaluated: "
                               "VPN peers are not restricted. Apply the current vpn/firewall/docker-user.sh.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Project D deployment reconciliation")
    parser.add_argument("--env-file", type=Path, help="optional .env used to render compose.yml (values are not compared)")
    args = parser.parse_args(argv)
    if not shutil.which("docker"):
        print("docker is required", file=sys.stderr)
        return 2
    report = Report()
    try:
        model = expected_model(args.env_file)
        containers = find_containers()
        report.out(f"# Project D reconciliation — repository project `{model.get('name')}`")
        report.out()
        check_services(model, containers, report)
        check_host(report)
    except RuntimeError as exc:
        print(f"Cannot run: {exc}", file=sys.stderr)
        return 2
    report.out()
    report.out("## Findings")
    report.out()
    order = {"HIGH": 0, "MEDIUM": 1, "INFO": 2}
    for level, text in sorted(report.findings, key=lambda f: order[f[0]]):
        report.out(f"- **{level}** {text}")
    if not report.findings:
        report.out("- none")
    print("\n".join(report.lines))
    return 1 if any(level == "HIGH" for level, _ in report.findings) else 0


if __name__ == "__main__":
    sys.exit(main())
