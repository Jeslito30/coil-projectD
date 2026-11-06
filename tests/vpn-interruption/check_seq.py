#!/usr/bin/env python3
"""Analyse per-sensor sequence gaps, resets, ordering and advisory duplicates."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def load_rows(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        if path.suffix.lower() == ".jsonl":
            return [json.loads(line) for line in handle if line.strip()]
        if path.suffix.lower() == ".json":
            data = json.load(handle)
            if isinstance(data, dict):
                data = data.get("points", data.get("rows", []))
            if not isinstance(data, list):
                raise ValueError("JSON must be a list or contain points/rows list")
            return data
        lines = [line for line in handle if line.strip() and not line.startswith("#")]
        return list(csv.DictReader(lines))


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp without timezone: {value}")
    return parsed.astimezone(timezone.utc)


def parse_window(value: str) -> tuple[datetime, datetime]:
    try:
        start_text, end_text = value.split(",", 1)
        start, end = parse_utc(start_text), parse_utc(end_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Use START,END in RFC3339 UTC, e.g. 2026-11-10T08:00:00Z,2026-11-10T08:05:00Z ({exc})"
        ) from exc
    if end < start:
        raise argparse.ArgumentTypeError(f"Window end is before start: {value}")
    return start, end


def in_any_window(row_time: str, windows: list[tuple[datetime, datetime]]) -> bool:
    if not windows or not row_time:
        return False
    moment = parse_utc(row_time)
    return any(start <= moment <= end for start, end in windows)


def normalise(rows: list[dict], default_run: str,
              exclude: list[tuple[datetime, datetime]] | None = None) -> tuple[list[dict], int]:
    points = []
    excluded = 0
    for index, row in enumerate(rows, 1):
        row_time = row.get("timestamp_utc") or row.get("_time") or ""
        try:
            if in_any_window(row_time, exclude or []):
                # Security-test messages and other recorded test windows are not simulator data.
                excluded += 1
                continue
        except ValueError as exc:
            raise ValueError(f"Row {index}: {exc}") from exc
        sensor = row.get("sensor_id")
        if not sensor:
            raise ValueError(f"Row {index}: missing sensor_id")
        try:
            seq = int(row["seq"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Row {index}: seq must be an integer") from exc
        if seq < 0:
            raise ValueError(f"Row {index}: seq must be non-negative")
        points.append({
            "sensor_id": sensor,
            "run_id": str(row.get("run_id") or default_run),
            "seq": seq,
            "time": row_time,
            "input_order": index,
        })
    return points, excluded


def analyse(points: list[dict]) -> dict:
    groups = defaultdict(list)
    for point in points:
        groups[(point["sensor_id"], point["run_id"])].append(point)
    summaries = []
    events = []
    for (sensor, run), rows in sorted(groups.items()):
        # Input order is intentional: document whether the export was chronological.
        previous = None
        seen = set()
        counts = defaultdict(int)
        for point in rows:
            seq = point["seq"]
            base = {"sensor_id": sensor, "run_id": run, "seq": seq, "time": point["time"]}
            # A return to 0 after a higher value is a simulator restart, not a duplicate.
            # It must be checked first because 0 is always already in `seen`.
            if previous is not None and seq == 0 and previous != 0:
                counts["resets"] += 1
                events.append({**base, "type": "sequence_reset", "previous_seq": previous})
                seen.clear()
                seen.add(seq)
                previous = seq
                continue
            if seq in seen:
                counts["duplicates"] += 1
                events.append({**base, "type": "duplicate_observed_in_input"})
                continue
            if previous is not None and seq > previous + 1:
                missing = seq - previous - 1
                counts["gaps"] += 1
                counts["missing_values"] += missing
                events.append({
                    **base, "type": "gap", "previous_seq": previous,
                    "missing_from": previous + 1, "missing_to": seq - 1,
                    "missing_count": missing,
                })
            elif previous is not None and seq < previous:
                counts["out_of_order"] += 1
                events.append({**base, "type": "out_of_order", "previous_seq": previous})
                seen.add(seq)
                continue
            seen.add(seq)
            previous = seq
        summaries.append({
            "sensor_id": sensor,
            "run_id": run,
            "input_rows": len(rows),
            **dict(counts),
        })
    return {"groups": summaries, "events": events}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="CSV, JSON or JSONL with sensor_id and seq")
    parser.add_argument("--run-id", default="run-1", help="Default run when rows have no run_id")
    parser.add_argument("--output", type=Path, help="Write complete JSON analysis here")
    parser.add_argument("--strict", action="store_true", help="Exit 1 when gaps/duplicates/resets/out-of-order exist")
    parser.add_argument(
        "--exclude-window", action="append", type=parse_window, default=[], metavar="START,END",
        help="Skip rows inside this UTC window (repeatable), e.g. the test_window_utc "
             "printed by tests/security/mqtt_security_test.py",
    )
    args = parser.parse_args()
    try:
        points, excluded = normalise(load_rows(args.input), args.run_id, args.exclude_window)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 2
    result = analyse(points)
    result["excluded_rows"] = excluded
    result["excluded_windows"] = [
        {"start": start.isoformat().replace("+00:00", "Z"), "end": end.isoformat().replace("+00:00", "Z")}
        for start, end in args.exclude_window
    ]
    result["notes"] = [
        "Rows are analysed in input order within sensor_id/run_id; use chronological exports and a new run_id after restart.",
        "Duplicate analysis is advisory only. InfluxDB can overwrite duplicate points, so absence here cannot prove no QoS 1 duplicate occurred.",
        "A gap is not attributed to VPN loss until restart and queue-overflow logs have been checked.",
    ]
    payload = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(json.dumps(result["groups"], indent=2, ensure_ascii=False))
    print(f"Events: {len(result['events'])}; excluded rows: {excluded}")
    if args.output:
        print(f"Wrote {args.output}")
    if args.strict and result["events"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
