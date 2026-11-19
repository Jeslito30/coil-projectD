#!/usr/bin/env python3
"""Create least-privilege InfluxDB tokens for Telegraf (write `sensors`) and Grafana (read `sensors`).

Until this runs, both services use the operator token INFLUX_TOKEN, which can read, write and delete
everything in InfluxDB. The script:
  1. finds the `sensors` bucket with the operator token (inside the influxdb container);
  2. creates "project-d telegraf (write sensors)" and "project-d grafana (read sensors)" tokens,
     or refuses if they already exist (--rotate creates new ones and prints the old IDs to delete
     once the recreated services work);
  3. writes INFLUX_TELEGRAF_TOKEN and INFLUX_GRAFANA_TOKEN into the .env file (mode kept at 600);
  4. prints the command that recreates Telegraf and Grafana. It never prints a token.

compose.yml falls back to INFLUX_TOKEN when the scoped variables are absent, so the stack keeps working
before and after this step. The operator token stays in .env for backup/restore and administration.

    sudo python3 deployment/contabo/scoped_tokens.py --env-file deployment/contabo/.env --dry-run
    sudo python3 deployment/contabo/scoped_tokens.py --env-file deployment/contabo/.env
    # an existing stack started under another Compose project name:
    sudo python3 deployment/contabo/scoped_tokens.py --env-file /path/.env --project-name coil-checkin2
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "deployment" / "contabo" / "compose.yml"
ROLES = {
    "INFLUX_TELEGRAF_TOKEN": ("project-d telegraf (write sensors)", "--write-bucket"),
    "INFLUX_GRAFANA_TOKEN": ("project-d grafana (read sensors)", "--read-bucket"),
}


def read_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, value = stripped.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def update_env(path: Path, updates: dict[str, str]) -> None:
    """Replaces or appends KEY=value lines, keeping every other line and the file mode."""
    lines = path.read_text(encoding="utf-8").splitlines()
    remaining = dict(updates)
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    if remaining:
        out.append("# Least-privilege InfluxDB tokens (deployment/contabo/scoped_tokens.py)")
        out.extend(f"{key}={value}" for key, value in remaining.items())
    mode = path.stat().st_mode & 0o777
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".env.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(out) + "\n")
        os.chmod(tmp, mode)
        try:
            st = path.stat()
            os.chown(tmp, st.st_uid, st.st_gid)
        except PermissionError:
            pass
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class Influx:
    def __init__(self, env_file: Path, project_name: str | None, operator_token: str, org: str):
        self.base = ["docker", "compose", "--env-file", str(env_file), "-f", str(COMPOSE_FILE)]
        if project_name:
            self.base += ["-p", project_name]
        self.token, self.org = operator_token, org

    def run(self, *args: str) -> object:
        cmd = [*self.base, "exec", "-T", "influxdb", "influx", *args, "--org", self.org, "--token", self.token, "--json"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            message = result.stderr.replace(self.token, "<operator-token>").strip()
            raise RuntimeError(f"influx {args[0]} {args[1]} failed: {message[:400]}")
        return json.loads(result.stdout or "null")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create least-privilege InfluxDB tokens for Project D")
    parser.add_argument("--env-file", type=Path, default=ROOT / "deployment" / "contabo" / ".env")
    parser.add_argument("--project-name", help="Compose project name of the running stack (e.g. coil-checkin2)")
    parser.add_argument("--bucket", default="sensors")
    parser.add_argument("--rotate", action="store_true", help="replace existing Project D tokens")
    parser.add_argument("--dry-run", action="store_true", help="show what would happen; change nothing")
    args = parser.parse_args(argv)

    env = read_env(args.env_file)
    operator = env.get("INFLUX_TOKEN") or sys.exit("INFLUX_TOKEN (operator token) missing from the env file")
    org = env.get("INFLUX_ORG", "coil")
    influx = Influx(args.env_file, args.project_name, operator, org)
    try:
        buckets = influx.run("bucket", "list", "--name", args.bucket) or []
        if len(buckets) != 1:
            print(f"Expected exactly one bucket named {args.bucket!r}, found {len(buckets)}.", file=sys.stderr)
            return 2
        bucket_id = buckets[0]["id"]
        existing = {a.get("description"): a for a in (influx.run("auth", "list") or [])}
        plan = []
        for variable, (description, flag) in ROLES.items():
            old = existing.get(description)
            if old and not args.rotate:
                print(f"{description!r} already exists (id {old['id']}). Use --rotate to replace it.", file=sys.stderr)
                return 2
            plan.append((variable, description, flag, old))
        for variable, description, flag, old in plan:
            action = "rotate" if old else "create"
            print(f"{'Would ' if args.dry_run else ''}{action}: {description} -> {variable}")
        if args.dry_run:
            return 0

        new_tokens, old_ids = {}, []
        for variable, description, flag, old in plan:
            created = influx.run("auth", "create", flag, bucket_id, "--description", description)
            created = created[0] if isinstance(created, list) else created
            if not created or not created.get("token"):
                raise RuntimeError(f"influx did not return a token for {description!r}")
            new_tokens[variable] = created["token"]
            if old:
                old_ids.append(old["id"])
        update_env(args.env_file, new_tokens)
        print(f"Wrote {', '.join(new_tokens)} to {args.env_file} (mode {oct(args.env_file.stat().st_mode & 0o777)}).")
    except (RuntimeError, KeyError, json.JSONDecodeError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1

    project = f" -p {args.project_name}" if args.project_name else ""
    print("Next: recreate the two services so they pick up the new tokens, then confirm new points arrive:\n"
          f"  docker compose --env-file {args.env_file} -f {COMPOSE_FILE}{project} up -d --no-deps telegraf grafana")
    if old_ids:
        # Deleting before the services are recreated would break writes; leave it as a separate step.
        print("After new points arrive and the dashboard loads, delete the replaced tokens:")
        for old_id in old_ids:
            print(f"  docker compose ... exec influxdb influx auth delete --id {old_id} --token <operator token>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
