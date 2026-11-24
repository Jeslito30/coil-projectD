"""Tests for team-dashboard/server.py against an in-process stand-in of InfluxDB's /api/v2/query.

The stand-in answers the three fixed Flux queries with annotated CSV in InfluxDB 2.x's format, checks
the read-only token header and records the queries, so the tests can assert that no request parameter
ever reaches Flux.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "team-dashboard" / "server.py"
TOKEN = secrets.token_hex(16)


def ts(delta_seconds: float) -> str:
    moment = datetime.now(timezone.utc) - timedelta(seconds=delta_seconds)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def annotated(columns, rows):
    head = ["#datatype,string,long," + ",".join("string" for _ in columns), "#group,false,false," + ",".join("false" for _ in columns),
            "#default,_result,," + ",".join("" for _ in columns), ",result,table," + ",".join(columns)]
    body = [",,0," + ",".join(str(v) for v in row) for row in rows]
    return "\r\n".join(head + body) + "\r\n\r\n"


class FakeInflux:
    def __init__(self):
        self.queries, self.fail = [], False
        self.latest = {  # sensor_id: (age_seconds, value)
            "MATI-TEMP-01": (5, 29.4), "MATI-HUM-01": (6, 81.2), "MATI-AIR-01": (4, 60.1),   # AIR out of range (>55.5)
            "SZCZ-TEMP-01": (300, 6.1), "SZCZ-HUM-01": (3, 77.0),                          # TEMP silent; AIR missing
            "ROGUE-01": (1, 1.0),                                                            # not in registry
        }


def make_handler(state: FakeInflux):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            if self.headers.get("Authorization") != f"Token {TOKEN}" or state.fail:
                self.send_response(401); self.end_headers(); return
            flux = self.rfile.read(int(self.headers["Content-Length"])).decode()
            state.queries.append(flux)
            if '_field == "delay_ms"' in flux:
                text = annotated(["sensor_id", "_value"], [("MATI-TEMP-01", 412.5), ("SZCZ-HUM-01", 38.0)])
            elif "aggregateWindow" in flux:
                rows = [(sid, ts(60 * i), v + i * 0.1) for sid, (_, v) in state.latest.items() for i in range(5, 0, -1)]
                text = annotated(["sensor_id", "_time", "_value"], rows)
            else:
                rows = [(sid, ts(age), v) for sid, (age, v) in state.latest.items()]
                text = annotated(["sensor_id", "_time", "_value"], rows)
            raw = text.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/csv"); self.send_header("Content-Length", str(len(raw)))
            self.end_headers(); self.wfile.write(raw)

    return Handler


class DashboardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = FakeInflux()
        cls.influx = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.state))
        threading.Thread(target=cls.influx.serve_forever, daemon=True).start()
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0)); cls.port = s.getsockname()[1]
        env = dict(os.environ, INFLUX_URL=f"http://127.0.0.1:{cls.influx.server_address[1]}", INFLUX_TOKEN=TOKEN,
                   INFLUX_ORG="coil", LISTEN_PORT=str(cls.port), CACHE_SECONDS="0")
        cls.proc = subprocess.Popen([sys.executable, str(SERVER)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{cls.port}/healthz", timeout=0.5); break
            except OSError:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate(); cls.proc.wait(5)
        cls.proc.stdout.close(); cls.proc.stderr.close()
        cls.influx.shutdown(); cls.influx.server_close()

    def get(self, path):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def summary(self):
        code, _, body = self.get("/api/summary")
        self.assertEqual(code, 200, body)
        return {s["sensor_id"]: s for s in json.loads(body)["sensors"]}, json.loads(body)

    def test_statuses(self):
        sensors, data = self.summary()
        self.assertEqual(len(sensors), 6, "all registered sensors, nothing else")
        self.assertNotIn("ROGUE-01", sensors)
        self.assertEqual(sensors["MATI-TEMP-01"]["status"], "ok")
        self.assertEqual(sensors["MATI-AIR-01"]["status"], "out_of_range")
        self.assertEqual(sensors["SZCZ-TEMP-01"]["status"], "silent")
        self.assertEqual(sensors["SZCZ-AIR-01"]["status"], "no_data")
        self.assertEqual(data["overall"], "attention")
        self.assertEqual(sensors["MATI-TEMP-01"]["delay_ms_10m"], 412.5)
        self.assertEqual(len(sensors["MATI-HUM-01"]["history"]), 5)
        self.assertLess(sensors["MATI-TEMP-01"]["age_seconds"], 30)

    def test_query_parameters_never_reach_flux(self):
        self.state.queries.clear()
        self.get('/api/summary?bucket=x")|>drop()&org=evil')
        self.assertEqual(len(self.state.queries), 3)
        for flux in self.state.queries:
            self.assertNotIn("drop()", flux)
            self.assertIn('from(bucket: "sensors")', flux)

    def test_security_headers_and_static(self):
        for path, kind in (("/", "text/html"), ("/app.js", "text/javascript"), ("/style.css", "text/css")):
            code, headers, body = self.get(path)
            self.assertEqual(code, 200)
            self.assertTrue(headers["Content-Type"].startswith(kind))
            self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
            self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(self.get("/../server.py")[0], 404)
        self.assertEqual(self.get("/static/../server.py")[0], 404)

    def test_influx_failure_returns_503_without_token(self):
        self.state.fail = True
        try:
            code, _, body = self.get("/api/summary")
        finally:
            self.state.fail = False
        self.assertEqual(code, 503)
        self.assertNotIn(TOKEN, body.decode())
        self.assertIn("HTTP 401", body.decode())


if __name__ == "__main__":
    unittest.main()
