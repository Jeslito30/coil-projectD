#!/usr/bin/env python3
"""Fetch delay_ms from InfluxDB v2 and write per-city/per-sensor CSV + Markdown."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def flux_range_value(value: str) -> str:
    if re.fullmatch(r"-[0-9]+(?:ns|us|ms|s|m|h|d|w)", value):
        return value
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Use a simple negative Flux duration (for example -24h) or RFC3339 time: {value}"
        ) from exc
    return f"time(v: {json.dumps(value)})"


def fetch_csv(args: argparse.Namespace, token: str) -> str:
    stop = f", stop: {flux_range_value(args.stop)}" if args.stop else ""
    query = f'''from(bucket: {json.dumps(args.bucket)})
  |> range(start: {flux_range_value(args.start)}{stop})
  |> filter(fn: (r) => r._measurement == "environment" and r._field == "delay_ms")
  |> keep(columns: ["_time", "_value", "city", "sensor_id", "sensor_type"])'''
    url = f"{args.url.rstrip('/')}/api/v2/query?{urllib.parse.urlencode({'org': args.org})}"
    request = urllib.request.Request(
        url,
        data=query.encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Token {token}",
            "Accept": "application/csv",
            "Content-Type": "application/vnd.flux",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            return response.read().decode("utf-8-sig")
    except urllib.error.HTTPError as exc:
        body = exc.read(4096).decode("utf-8", errors="replace")
        raise RuntimeError(f"InfluxDB HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"InfluxDB connection failed: {exc.reason}") from exc


def parse_points(payload: str) -> list[dict]:
    # Influx annotated CSV comments describe datatypes/groups and are not rows.
    lines = [line for line in payload.splitlines() if line.strip() and not line.startswith("#")]
    if not lines:
        return []
    points = []
    for row in csv.DictReader(lines):
        if row.get("_value") in (None, "", "_value"):
            continue
        try:
            value = float(row["_value"])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value):
            continue
        points.append({
            "city": row.get("city") or "unknown",
            "sensor_id": row.get("sensor_id") or "unknown",
            "time": row.get("_time") or "",
            "delay_ms": value,
        })
    return points


def percentile95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def summarise(points: list[dict]) -> list[dict]:
    groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    for point in points:
        groups[("city", point["city"])].append(point["delay_ms"])
        groups[("sensor", point["sensor_id"])].append(point["delay_ms"])
    rows = []
    for (scope, key), values in sorted(groups.items()):
        rows.append({
            "scope": scope,
            "key": key,
            "count": len(values),
            "mean_ms": statistics.fmean(values),
            "median_ms": statistics.median(values),
            "p95_ms": percentile95(values),
            "min_ms": min(values),
            "max_ms": max(values),
            "negative_count": sum(value < 0 for value in values),
        })
    return rows


def write_outputs(rows: list[dict], prefix: Path, metadata: dict) -> None:
    prefix.parent.mkdir(parents=True, exist_ok=True)
    fields = ["scope", "key", "count", "mean_ms", "median_ms", "p95_ms", "min_ms", "max_ms", "negative_count"]
    csv_path = prefix.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            formatted = dict(row)
            for field in fields[3:7]:
                formatted[field] = f"{row[field]:.3f}"
            writer.writerow(formatted)

    md_path = prefix.with_suffix(".md")
    with md_path.open("w", encoding="utf-8") as handle:
        handle.write("# Project D — delay analysis\n\n")
        handle.write(f"- Generated UTC: `{metadata['generated_utc']}`\n")
        handle.write(f"- Window: `{metadata['start']}` to `{metadata['stop'] or 'now'}`\n")
        handle.write(f"- Scenario: **{metadata['scenario']}** (separate normal and recovery runs)\n")
        handle.write("- Metric: Telegraf processing time minus payload timestamp; not pure network latency\n")
        handle.write("- p95 method: nearest-rank; negative values retained as clock diagnostics\n\n")
        handle.write("| Scope | City / sensor | Count | Mean ms | Median ms | p95 ms | Min ms | Max ms | Negative |\n")
        handle.write("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |\n")
        for row in rows:
            handle.write(
                f"| {row['scope']} | {row['key']} | {row['count']} | {row['mean_ms']:.3f} | "
                f"{row['median_ms']:.3f} | {row['p95_ms']:.3f} | {row['min_ms']:.3f} | "
                f"{row['max_ms']:.3f} | {row['negative_count']} |\n"
            )
        handle.write("\nAttach chrony offsets, simulator restart times and the exact query window to this report.\n")
    print(f"Wrote {csv_path} and {md_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("INFLUX_URL", "http://influxdb:8086"))
    parser.add_argument("--org", default=os.environ.get("INFLUX_ORG", "coil"))
    parser.add_argument("--bucket", default=os.environ.get("INFLUX_BUCKET", "coil"))
    parser.add_argument("--start", default="-1h", help="Negative Flux duration or RFC3339 time")
    parser.add_argument("--stop", help="Optional negative Flux duration or RFC3339 time")
    parser.add_argument("--scenario", choices=["normal", "interruption", "recovery", "other"], default="normal")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--output", type=Path, help="Output prefix without .csv/.md suffix")
    args = parser.parse_args()
    token = os.environ.get("INFLUX_TOKEN")
    if not token:
        parser.error("INFLUX_TOKEN is required")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    prefix = args.output or Path(__file__).parent / "results" / f"delay-{args.scenario}-{timestamp}"
    try:
        points = parse_points(fetch_csv(args, token))
    except (RuntimeError, argparse.ArgumentTypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not points:
        print("No finite delay_ms points found for this window; no report written.", file=sys.stderr)
        return 4
    rows = summarise(points)
    write_outputs(rows, prefix, {
        "generated_utc": timestamp,
        "start": args.start,
        "stop": args.stop,
        "scenario": args.scenario,
    })
    print(f"Analysed {len(points)} points in {len(rows)} groups; scenario={args.scenario}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
