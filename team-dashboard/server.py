#!/usr/bin/env python3
"""Project D Team Dashboard v1: a read-only live view of both cities, served only inside the VPN.

Design:
  * Python standard library only; runs in a stock python:3.12-alpine container with a read-only filesystem.
  * The browser never talks to InfluxDB. This server runs three FIXED Flux queries with a read-only token
    and returns JSON at /api/summary. No request parameter reaches a query, so there is no injection path.
  * Results are cached for CACHE_SECONDS so page refreshes cannot load InfluxDB.
  * Every sensor in the registry is always listed, so a silent or missing sensor is visible, not hidden.

Environment:
  INFLUX_URL (default http://influxdb:8086), INFLUX_ORG (default coil), INFLUX_BUCKET (default sensors),
  INFLUX_TOKEN (read-only token for the bucket), LISTEN_PORT (default 8088), CACHE_SECONDS (default 5),
  INFLUX_MEASUREMENT (environment), VALUE_FIELD (value), DELAY_FIELD (delay_ms) for a differently named schema.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
VERSION = "1.0"
SILENT_AFTER_SECONDS = 120  # same as Grafana "Alert 2 - sensor silent"

# Registry (documents/data-design.md) with the thresholds of Grafana "Alert 1 - threshold".
SENSORS = [
    {"sensor_id": "MATI-TEMP-01", "city": "Mati", "sensor_type": "temperature", "unit": "°C", "low": 20.0, "high": 38.0},
    {"sensor_id": "MATI-HUM-01", "city": "Mati", "sensor_type": "humidity", "unit": "%RH", "low": 40.0, "high": 100.0},
    {"sensor_id": "MATI-AIR-01", "city": "Mati", "sensor_type": "airquality", "unit": "µg/m³", "low": -1.0, "high": 55.5},
    {"sensor_id": "SZCZ-TEMP-01", "city": "Szczecin", "sensor_type": "temperature", "unit": "°C", "low": -15.0, "high": 20.0},
    {"sensor_id": "SZCZ-HUM-01", "city": "Szczecin", "sensor_type": "humidity", "unit": "%RH", "low": 40.0, "high": 100.0},
    {"sensor_id": "SZCZ-AIR-01", "city": "Szczecin", "sensor_type": "airquality", "unit": "µg/m³", "low": -1.0, "high": 75.0},
]
REGISTERED = {s["sensor_id"] for s in SENSORS}


# Schema names, configurable for a stack whose Telegraf uses other names. Validated at start-up so an
# environment value can never change the structure of a query.
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
SCHEMA = {
    "bucket": os.environ.get("INFLUX_BUCKET", "sensors"),
    "measurement": os.environ.get("INFLUX_MEASUREMENT", "environment"),
    "value": os.environ.get("VALUE_FIELD", "value"),
    "delay": os.environ.get("DELAY_FIELD", "delay_ms"),
}


def flux_latest(bucket: str) -> str:
    return f'''from(bucket: "{bucket}")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "{SCHEMA['measurement']}" and r._field == "{SCHEMA['value']}")
  |> group(columns: ["sensor_id"])
  |> last()
  |> keep(columns: ["sensor_id", "_time", "_value"])'''


def flux_history(bucket: str) -> str:
    return f'''from(bucket: "{bucket}")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "{SCHEMA['measurement']}" and r._field == "{SCHEMA['value']}")
  |> group(columns: ["sensor_id"])
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
  |> keep(columns: ["sensor_id", "_time", "_value"])'''


def flux_delay(bucket: str) -> str:
    return f'''from(bucket: "{bucket}")
  |> range(start: -10m)
  |> filter(fn: (r) => r._measurement == "{SCHEMA['measurement']}" and r._field == "{SCHEMA['delay']}")
  |> group(columns: ["sensor_id"])
  |> mean()
  |> keep(columns: ["sensor_id", "_value"])'''


def parse_annotated_csv(text: str) -> list[dict[str, str]]:
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


def parse_time(text: str) -> datetime:
    text = text.rstrip("Z")
    if "." in text:
        base, frac = text.split(".", 1)
        text = f"{base}.{frac[:6]}"
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=timezone.utc)
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


class Influx:
    def __init__(self, url: str, org: str, token: str, timeout: float = 8.0):
        self.endpoint = url.rstrip("/") + "/api/v2/query?" + urllib.parse.urlencode({"org": org})
        self.headers = {"Authorization": f"Token {token}", "Content-Type": "application/vnd.flux",
                        "Accept": "application/csv"}
        self.timeout = timeout

    def query(self, flux: str) -> list[dict[str, str]]:
        request = urllib.request.Request(self.endpoint, data=flux.encode(), method="POST", headers=self.headers)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return parse_annotated_csv(response.read().decode("utf-8"))


def build_summary(influx: Influx, bucket: str, now: datetime) -> dict:
    latest = {r["sensor_id"]: r for r in influx.query(flux_latest(bucket)) if r.get("sensor_id") in REGISTERED}
    history: dict[str, list] = {}
    for row in influx.query(flux_history(bucket)):
        if row.get("sensor_id") in REGISTERED:
            history.setdefault(row["sensor_id"], []).append([row["_time"], round(float(row["_value"]), 2)])
    delay = {r["sensor_id"]: float(r["_value"]) for r in influx.query(flux_delay(bucket))
             if r.get("sensor_id") in REGISTERED and r.get("_value") not in (None, "")}

    sensors = []
    for spec in SENSORS:
        item = dict(spec)
        row = latest.get(spec["sensor_id"])
        if row is None:
            item.update(status="no_data", value=None, time=None, age_seconds=None)
        else:
            value = float(row["_value"])
            age = (now - parse_time(row["_time"])).total_seconds()
            if age > SILENT_AFTER_SECONDS:
                status = "silent"
            elif value < spec["low"] or value > spec["high"]:
                status = "out_of_range"
            else:
                status = "ok"
            item.update(status=status, value=round(value, 2), time=row["_time"], age_seconds=round(age, 1))
        item["history"] = history.get(spec["sensor_id"], [])
        item["delay_ms_10m"] = round(delay[spec["sensor_id"]], 1) if spec["sensor_id"] in delay else None
        sensors.append(item)

    counts = {}
    for item in sensors:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    overall = "ok" if counts.get("ok", 0) == len(sensors) else "attention"
    return {"version": VERSION, "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "overall": overall,
            "counts": counts, "silent_after_seconds": SILENT_AFTER_SECONDS, "sensors": sensors}


class Cache:
    def __init__(self, ttl: float):
        self.ttl, self.lock, self.value, self.stamp = ttl, threading.Lock(), None, 0.0

    def get(self, producer):
        with self.lock:
            if self.value is None or time.monotonic() - self.stamp > self.ttl:
                self.value = producer()
                self.stamp = time.monotonic()
            return self.value


SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                               "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}


def make_handler(influx: Influx, bucket: str, cache: Cache):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ProjectD-TeamDashboard"
        sys_version = ""

        def log_message(self, fmt, *args):
            sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

        def send(self, code: int, body: bytes, content_type: str):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for key, value in SECURITY_HEADERS.items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in STATIC:
                name, content_type = STATIC[path]
                return self.send(200, (HERE / "static" / name).read_bytes(), content_type)
            if path == "/healthz":
                return self.send(200, b"ok\n", "text/plain; charset=utf-8")
            if path == "/api/summary":
                try:
                    data = cache.get(lambda: build_summary(influx, bucket, datetime.now(timezone.utc)))
                except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
                    # Never echo request details or the token; the reason class is enough to troubleshoot.
                    detail = f"HTTP {exc.code}" if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__
                    self.log_message("InfluxDB query failed: %s", detail)
                    body = json.dumps({"error": "data source unavailable", "detail": detail}).encode()
                    return self.send(503, body, "application/json")
                return self.send(200, json.dumps(data).encode(), "application/json")
            self.send(404, b"not found\n", "text/plain; charset=utf-8")

    return Handler


def main() -> int:
    token = os.environ.get("INFLUX_TOKEN", "")
    if not token:
        print("INFLUX_TOKEN (read-only token for the sensors bucket) is required", file=sys.stderr)
        return 2
    for key, value in SCHEMA.items():
        if not NAME_RE.match(value):
            print(f"Invalid {key} name {value!r}: letters, digits, '_', '.', '-' only", file=sys.stderr)
            return 2
    bucket = SCHEMA["bucket"]
    influx = Influx(os.environ.get("INFLUX_URL", "http://influxdb:8086"), os.environ.get("INFLUX_ORG", "coil"), token)
    cache = Cache(float(os.environ.get("CACHE_SECONDS", "5")))
    port = int(os.environ.get("LISTEN_PORT", "8088"))
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(influx, bucket, cache))
    server.daemon_threads = True
    print(f"Project D Team Dashboard v{VERSION} listening on :{port} (bucket {bucket})", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
