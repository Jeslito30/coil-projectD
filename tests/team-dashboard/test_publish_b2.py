"""Offline tests for team-dashboard/publish_b2.py against stand-ins of the B2 native API (v3) and InfluxDB.

Checks: the upload protocol (authorize, get_upload_url, upload with SHA1 and cache headers), reuse of the
upload URL, re-authorization after an expired token, the mirror page rewrite, and that no secret or
internal host name is ever written to the public bucket.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "team-dashboard"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish_b2  # noqa: E402
import test_server as fake_influx  # noqa: E402  (reuses the InfluxDB stand-in)

KEY_ID, APP_KEY = "k" + secrets.token_hex(6), secrets.token_hex(16)


class FakeB2:
    def __init__(self):
        self.files, self.calls, self.auth_count, self.expire_next_upload = {}, [], 0, False
        self.token = None


def make_b2_handler(state: FakeB2, base: list):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, code, body):
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            state.calls.append(self.path)
            if self.path == "/b2api/v3/b2_authorize_account":
                expected = "Basic " + base64.b64encode(f"{KEY_ID}:{APP_KEY}".encode()).decode()
                if self.headers.get("Authorization") != expected:
                    return self.reply(401, {"code": "unauthorized"})
                state.auth_count += 1
                state.token = secrets.token_hex(8)
                return self.reply(200, {"accountId": "acc", "authorizationToken": state.token, "apiInfo": {"storageApi": {
                    "apiUrl": base[0], "downloadUrl": base[0],
                    "allowed": {"buckets": [{"id": "bkt1", "name": "projectd-dashboard"}]}}}})
            self.reply(404, {})

        def do_POST(self):
            state.calls.append(self.path.split("?")[0])
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            if self.path == "/b2api/v3/b2_get_upload_url":
                if self.headers.get("Authorization") != state.token:
                    return self.reply(401, {"code": "expired_auth_token"})
                return self.reply(200, {"uploadUrl": base[0] + "/upload/bkt1", "authorizationToken": "up-" + state.token})
            if self.path == "/upload/bkt1":
                if state.expire_next_upload:
                    state.expire_next_upload = False
                    return self.reply(401, {"code": "expired_auth_token"})
                if self.headers.get("Authorization") != "up-" + state.token:
                    return self.reply(401, {"code": "bad_auth_token"})
                if hashlib.sha1(body).hexdigest() != self.headers["X-Bz-Content-Sha1"]:
                    return self.reply(400, {"code": "bad_request"})
                name = urllib.parse.unquote(self.headers["X-Bz-File-Name"])
                state.files[name] = {"body": body, "type": self.headers["Content-Type"],
                                     "cache": urllib.parse.unquote(self.headers.get("X-Bz-Info-b2-cache-control", ""))}
                return self.reply(200, {"fileName": name})
            self.reply(404, {})

    return Handler


class PublishTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.influx_state = fake_influx.FakeInflux()
        cls.influx = ThreadingHTTPServer(("127.0.0.1", 0), fake_influx.make_handler(cls.influx_state))
        threading.Thread(target=cls.influx.serve_forever, daemon=True).start()
        cls.b2_state, base = FakeB2(), [""]
        cls.b2_srv = ThreadingHTTPServer(("127.0.0.1", 0), make_b2_handler(cls.b2_state, base))
        base[0] = f"http://127.0.0.1:{cls.b2_srv.server_address[1]}"
        threading.Thread(target=cls.b2_srv.serve_forever, daemon=True).start()
        cls.env = {"INFLUX_URL": f"http://127.0.0.1:{cls.influx.server_address[1]}", "INFLUX_TOKEN": fake_influx.TOKEN,
                   "INFLUX_ORG": "coil", "B2_KEY_ID": KEY_ID, "B2_APP_KEY": APP_KEY, "B2_BUCKET": "projectd-dashboard",
                   "B2_API_BASE": base[0]}

    @classmethod
    def tearDownClass(cls):
        for srv in (cls.influx, cls.b2_srv):
            srv.shutdown()
            srv.server_close()

    def run_once(self):
        old = dict(os.environ)
        os.environ.update(self.env)
        try:
            return publish_b2.main(["--once"])
        finally:
            os.environ.clear()
            os.environ.update(old)

    def test_publishes_page_assets_and_summary(self):
        self.assertEqual(self.run_once(), 0)
        files = self.b2_state.files
        self.assertEqual(set(files), {"index.html", "app.js", "style.css", "summary.json"})
        self.assertEqual(files["summary.json"]["cache"], "public, max-age=30")
        summary = json.loads(files["summary.json"]["body"])
        self.assertTrue(summary["mirror"])
        self.assertEqual(len(summary["sensors"]), 6)
        html = files["index.html"]["body"].decode()
        self.assertIn('content="summary.json"', html)
        self.assertIn('content="public-mirror"', html)
        self.assertIn('src="app.js"', html)
        self.assertNotIn('src="/app.js"', html)

    def test_nothing_secret_or_internal_is_published(self):
        self.run_once()
        blob = b"".join(f["body"] for f in self.b2_state.files.values()).decode()
        for secret in (fake_influx.TOKEN, APP_KEY, KEY_ID, self.env["INFLUX_URL"], "influxdb:8086"):
            self.assertNotIn(secret, blob)

    def test_upload_url_is_reused_and_expired_token_recovers(self):
        self.b2_state.calls.clear()
        auth_before = self.b2_state.auth_count
        self.b2_state.expire_next_upload = True
        self.assertEqual(self.run_once(), 0)
        self.assertEqual(self.b2_state.auth_count - auth_before, 2, "initial authorize + one re-authorize after 401")
        self.assertLessEqual(self.b2_state.calls.count("/b2api/v3/b2_get_upload_url"), 2)
        self.assertEqual(self.b2_state.calls.count("/upload/bkt1"), 5, "4 files + 1 retried upload")

    def test_missing_configuration_is_refused(self):
        old = dict(os.environ)
        os.environ.pop("B2_APP_KEY", None)
        try:
            self.assertEqual(publish_b2.main(["--once"]), 2)
        finally:
            os.environ.clear()
            os.environ.update(old)


if __name__ == "__main__":
    unittest.main()
