"""Offline tests for grafana/viewer_accounts.py against a minimal in-process Grafana API stand-in.

The stand-in implements only the endpoints the script uses and the response shapes documented by
Grafana's HTTP API. It proves the script's control flow (create, re-run, role repair, admin guard,
dry run, password file handling); it is not a substitute for running the script against Grafana.
"""
from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import secrets
import stat
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("viewer_accounts", ROOT / "grafana" / "viewer_accounts.py")
viewer_accounts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(viewer_accounts)

ADMIN = ("admin", secrets.token_urlsafe(18))  # generated per run; no literal credential in Git


class FakeGrafana:
    def __init__(self):
        self.users = {1: {"id": 1, "login": "admin", "email": "admin@localhost", "isGrafanaAdmin": True}}
        self.org = {1: "Admin"}
        self.next_id = 2
        self.calls = []


def make_handler(state: FakeGrafana):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, body):
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def authorised(self):
            expected = "Basic " + base64.b64encode(f"{ADMIN[0]}:{ADMIN[1]}".encode()).decode()
            if self.headers.get("Authorization") != expected:
                self.reply(401, {"message": "Unauthorized"})
                return False
            return True

        def body(self):
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length)) if length else {}

        def do_GET(self):
            if not self.authorised():
                return
            url = urlparse(self.path)
            state.calls.append(("GET", url.path))
            if url.path == "/api/users/lookup":
                key = parse_qs(url.query)["loginOrEmail"][0]
                user = next((u for u in state.users.values() if key in (u["login"], u["email"])), None)
                return self.reply(200, user) if user else self.reply(404, {"message": "user not found"})
            if url.path == "/api/org/users":
                return self.reply(200, [{"userId": uid, "login": state.users[uid]["login"], "role": role}
                                        for uid, role in state.org.items()])
            self.reply(404, {"message": "not found"})

        def do_POST(self):
            if not self.authorised():
                return
            body = self.body()
            state.calls.append(("POST", self.path))
            if self.path == "/api/admin/users":
                if len(body.get("password", "")) < 4:
                    return self.reply(400, {"message": "password too short"})
                uid = state.next_id
                state.next_id += 1
                state.users[uid] = {"id": uid, "login": body["login"], "email": body["email"], "isGrafanaAdmin": False}
                state.org[uid] = "Viewer"  # auto_assign_org_role default
                return self.reply(200, {"id": uid, "message": "User created"})
            if self.path == "/api/org/users":
                user = next(u for u in state.users.values() if u["login"] == body["loginOrEmail"])
                state.org[user["id"]] = body["role"]
                return self.reply(200, {"message": "User added to organization"})
            self.reply(404, {"message": "not found"})

        def do_PATCH(self):
            if not self.authorised():
                return
            body = self.body()
            state.calls.append(("PATCH", self.path))
            uid = int(self.path.rsplit("/", 1)[1])
            state.org[uid] = body["role"]
            self.reply(200, {"message": "Organization user updated"})

    return Handler


class ViewerAccountsTest(unittest.TestCase):
    def setUp(self):
        self.state = FakeGrafana()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.state))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.users = self.dir / "viewers.csv"
        self.users.write_text("# faculty viewers\nlogin,email,name\n"
                              "faculty-dorsu,dorsu@example.org,DOrSU faculty\n"
                              "faculty-pm,pm@example.org,PM faculty\n", encoding="utf-8")
        self.passwords = self.dir / "out" / "passwords.txt"
        self.passwords.parent.mkdir()
        os.environ["GRAFANA_ADMIN_USER"], os.environ["GRAFANA_ADMIN_PASSWORD"] = ADMIN

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def run_script(self, *extra):
        out = io.StringIO()
        with redirect_stdout(out):
            code = viewer_accounts.main(["--users", str(self.users), "--url", self.url,
                                         "--passwords-out", str(self.passwords), *extra])
        return code, out.getvalue()

    def test_creates_viewers_and_writes_private_password_file(self):
        code, out = self.run_script()
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(u["login"] for u in self.state.users.values()), ["admin", "faculty-dorsu", "faculty-pm"])
        for uid, user in self.state.users.items():
            if user["login"] != "admin":
                self.assertEqual(self.state.org[uid], "Viewer")
        lines = self.passwords.read_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(stat.S_IMODE(self.passwords.stat().st_mode), 0o600)
        for line in lines:
            self.assertNotIn(line.split("\t")[2], out, "passwords must not be printed")

    def test_rerun_is_idempotent_and_repairs_role(self):
        self.run_script()
        uid = next(u["id"] for u in self.state.users.values() if u["login"] == "faculty-pm")
        self.state.org[uid] = "Editor"
        before = len(self.state.users)
        code, out = self.run_script()
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.state.users), before, "no duplicate users")
        self.assertEqual(self.state.org[uid], "Viewer")
        self.assertIn("Editor -> Viewer", out)
        self.assertEqual(len(self.passwords.read_text().splitlines()), 2, "existing users get no new password")

    def test_refuses_to_touch_server_admin(self):
        self.users.write_text("login,email,name\nadmin,admin@localhost,Admin\n", encoding="utf-8")
        code, out = self.run_script()
        self.assertEqual(code, 1)
        self.assertIn("server admin", out)
        self.assertEqual(self.state.org[1], "Admin")

    def test_dry_run_changes_nothing(self):
        code, out = self.run_script("--dry-run")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.state.users), 1)
        self.assertFalse(self.passwords.exists())
        self.assertNotIn("POST", [method for method, _ in self.state.calls])

    def test_wrong_admin_password_fails_cleanly(self):
        os.environ["GRAFANA_ADMIN_PASSWORD"] = secrets.token_hex(8)  # random wrong value
        code, out = self.run_script()
        self.assertEqual(code, 1)
        self.assertIn("HTTP 401", out)

    def test_rejects_bad_csv(self):
        self.users.write_text("login,email,name\nbad login,x@example.org,X\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.run_script()


if __name__ == "__main__":
    unittest.main()
