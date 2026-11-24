#!/usr/bin/env python3
"""Per-sensor InfluxDB inventory for backup/restore verification (tests/restore/restore-log.md).

  snapshot  Query the running stack and print a CSV inventory per sensor for a FIXED time window:
            points, first and newest timestamp, min/max value. Run it on the source server right
            after a backup, with --stop set to the backup time, and keep the CSV.
  compare   Compare two inventory CSVs (source vs restored). Every sensor in the source must be
            present in the restore with the same point count and the same newest timestamp.

    python3 tests/restore/inventory.py snapshot --stop 2026-11-19T02:30:00Z > before.csv     # source
    python3 tests/restore/inventory.py snapshot --stop 2026-11-19T02:30:00Z > after.csv      # restored VM
    python3 tests/restore/inventory.py compare before.csv after.csv

Use the SAME --start/--stop on both sides; a window that is still receiving data cannot match.
The token is read from deployment/contabo/.env and passed to the influx CLI inside the container;
it is never printed.
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COLUMNS = ["city", "sensor_id", "points", "first", "newest", "min", "max"]
RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")

FLUX = """
data = from(bucket: "{bucket}")
  |> range(start: {start}, stop: {stop})
  |> filter(fn: (r) => r._measurement == "environment" and r._field == "value")
  |> group(columns: ["city", "sensor_id"])
p_points = data |> count() |> map(fn: (r) => ({{city: r.city, sensor_id: r.sensor_id, metric: "points", v: string(v: r._value)}}))
p_first = data |> first() |> map(fn: (r) => ({{city: r.city, sensor_id: r.sensor_id, metric: "first", v: string(v: r._time)}}))
p_newest = data |> last() |> map(fn: (r) => ({{city: r.city, sensor_id: r.sensor_id, metric: "newest", v: string(v: r._time)}}))
p_min = data |> min() |> map(fn: (r) => ({{city: r.city, sensor_id: r.sensor_id, metric: "min", v: string(v: r._value)}}))
p_max = data |> max() |> map(fn: (r) => ({{city: r.city, sensor_id: r.sensor_id, metric: "max", v: string(v: r._value)}}))
union(tables: [p_points, p_first, p_newest, p_min, p_max]) |> group() |> yield(name: "inventory")
"""


def read_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def parse_annotated_csv(text: str) -> list[dict[str, str]]:
    """Parse `influx query --raw` output (annotated CSV, possibly several tables)."""
    rows, header = [], None
    for record in csv.reader(io.StringIO(text)):
        if not record or not any(cell.strip() for cell in record):
            header = None
            continue
        if record[0].startswith("#"):
            continue
        if header is None:
            header = record
            continue
        rows.append(dict(zip(header, record)))
    return rows


def pivot(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    sensors: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        key = (row["city"], row["sensor_id"])
        sensors.setdefault(key, {"city": key[0], "sensor_id": key[1]})[row["metric"]] = row["v"]
    return [sensors[key] for key in sorted(sensors, key=lambda k: (k[0], k[1]))]


def snapshot(args) -> int:
    for value in (args.start, args.stop):
        if not RFC3339.match(value):
            raise SystemExit(f"--start/--stop must be absolute UTC RFC3339 times such as 2026-11-19T02:30:00Z, got {value!r}")
    env = read_env(args.env_file)
    token = env.get(args.token_var) or sys.exit(f"{args.token_var} missing from the env file")
    org = env.get("INFLUX_ORG", "coil")
    flux = FLUX.format(bucket=args.bucket, start=args.start, stop=args.stop)
    cmd = ["docker", "compose", "--env-file", str(args.env_file), "-f", str(args.compose_file),
           "exec", "-T", "influxdb", "influx", "query", "--raw", "--org", org, "--token", token, flux]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr.replace(token, "<token>").strip(), file=sys.stderr)
        return 2
    write_inventory(pivot(parse_annotated_csv(result.stdout)), sys.stdout, args.start, args.stop)
    return 0


def write_inventory(sensors, handle, start, stop) -> None:
    handle.write(f"# window {start} .. {stop}\n")
    writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for sensor in sensors:
        writer.writerow({column: sensor.get(column, "") for column in COLUMNS})


def load_inventory(path: Path) -> tuple[str, dict[str, dict[str, str]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    window = lines[0][2:] if lines and lines[0].startswith("# ") else "(unknown window)"
    reader = csv.DictReader(line for line in lines if not line.startswith("#"))
    return window, {row["sensor_id"]: row for row in reader}


def compare(args) -> int:
    window_a, before = load_inventory(args.before)
    window_b, after = load_inventory(args.after)
    failures = 0
    sensor_failures = 0
    if window_a != window_b:
        print(f"FAIL  windows differ: {window_a} vs {window_b}")
        failures += 1
    if not before:
        print("FAIL  source inventory is empty")
        return 1
    for sensor_id, src in sorted(before.items()):
        dst = after.get(sensor_id)
        if dst is None:
            print(f"FAIL  {sensor_id}: missing after restore")
            failures += 1
            sensor_failures += 1
            continue
        problems = [f"{field} {src[field]} -> {dst[field]}" for field in ("points", "first", "newest", "min", "max")
                    if src.get(field) != dst.get(field)]
        if problems:
            print(f"FAIL  {sensor_id}: " + "; ".join(problems))
            failures += 1
            sensor_failures += 1
        else:
            print(f"PASS  {sensor_id}: {src['points']} points, newest {src['newest']}")
    for sensor_id in sorted(set(after) - set(before)):
        print(f"INFO  {sensor_id}: only present after restore")
    cities = {row["city"] for row in after.values()}
    print(f"Cities after restore: {', '.join(sorted(cities)) or 'none'}")
    print(f"Summary: {len(before) - sensor_failures} of {len(before)} sensors identical")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="InfluxDB inventory for restore verification")
    sub = parser.add_subparsers(dest="command", required=True)
    snap = sub.add_parser("snapshot")
    snap.add_argument("--env-file", type=Path, default=ROOT / "deployment/contabo/.env")
    snap.add_argument("--compose-file", type=Path, default=ROOT / "deployment/contabo/compose.yml",
                      help="compose file of the stack to query (e.g. the live coil-checkin2 one)")
    snap.add_argument("--token-var", default="INFLUX_TOKEN", help="variable in the env file holding the operator token")
    snap.add_argument("--bucket", default="sensors")
    snap.add_argument("--start", default="1970-01-01T00:00:00Z")
    snap.add_argument("--stop", required=True, help="absolute UTC end of the window, e.g. the backup time")
    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("before", type=Path)
    cmp_.add_argument("after", type=Path)
    args = parser.parse_args(argv)
    return snapshot(args) if args.command == "snapshot" else compare(args)


if __name__ == "__main__":
    sys.exit(main())
