"""Offline tests for deployment/contabo/scoped_tokens.py with a stand-in `docker compose exec influx`.

The stand-in keeps a tiny JSON state (buckets, authorizations) and answers the influx CLI calls the
script makes with JSON shaped like influx CLI 2.7 output. Not a substitute for a run on InfluxDB.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deployment" / "contabo" / "scoped_tokens.py"

FAKE_DOCKER = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, sys, secrets
    state_path = os.environ["FAKE_INFLUX_STATE"]
    state = json.load(open(state_path))
    argv = sys.argv[1:]
    open(state_path + ".log", "a").write(" ".join(argv) + "\\n")
    i = argv.index("influx")
    args = argv[i + 1:]
    def opt(name):
        return args[args.index(name) + 1] if name in args else None
    if opt("--token") != state["operator"]:
        sys.stderr.write("Error: unauthorized access\\n"); sys.exit(1)
    if args[:2] == ["bucket", "list"]:
        out = [b for b in state["buckets"] if b["name"] == opt("--name")]
    elif args[:2] == ["auth", "list"]:
        out = state["auths"]
    elif args[:2] == ["auth", "create"]:
        bucket = opt("--write-bucket") or opt("--read-bucket")
        kind = "write" if opt("--write-bucket") else "read"
        auth = {"id": secrets.token_hex(8), "description": opt("--description"), "token": secrets.token_urlsafe(32),
                "status": "active", "permissions": [kind + ":orgs/x/buckets/" + bucket]}
        state["auths"].append(auth); out = auth
    else:
        sys.stderr.write("unsupported\\n"); sys.exit(2)
    json.dump(state, open(state_path, "w"))
    print(json.dumps(out))
''')


class ScopedTokensTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        (d / "bin").mkdir()
        docker = d / "bin" / "docker"
        docker.write_text(FAKE_DOCKER)
        docker.chmod(0o755)
        self.operator = os.urandom(12).hex()
        self.state = d / "state.json"
        self.state.write_text(json.dumps({"operator": self.operator, "auths": [],
                                          "buckets": [{"id": "b0c3", "name": "sensors"}]}))
        self.env = d / ".env"
        self.env.write_text(f"# Project D\nINFLUX_ORG=coil\nINFLUX_TOKEN={self.operator}\nGRAFANA_ALERT_EMAIL=a@example.org\n")
        self.env.chmod(0o600)
        self.environ = dict(os.environ, PATH=f"{d / 'bin'}:{os.environ['PATH']}", FAKE_INFLUX_STATE=str(self.state))

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, *extra):
        return subprocess.run([sys.executable, str(SCRIPT), "--env-file", str(self.env), *extra],
                              capture_output=True, text=True, env=self.environ)

    def env_values(self):
        return dict(line.split("=", 1) for line in self.env.read_text().splitlines() if "=" in line and not line.startswith("#"))

    def test_creates_scoped_tokens_and_keeps_env_private(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        values = self.env_values()
        state = json.loads(self.state.read_text())
        by_desc = {a["description"]: a for a in state["auths"]}
        self.assertEqual(values["INFLUX_TELEGRAF_TOKEN"], by_desc["project-d telegraf (write sensors)"]["token"])
        self.assertEqual(values["INFLUX_GRAFANA_TOKEN"], by_desc["project-d grafana (read sensors)"]["token"])
        self.assertEqual(by_desc["project-d telegraf (write sensors)"]["permissions"], ["write:orgs/x/buckets/b0c3"])
        self.assertEqual(by_desc["project-d grafana (read sensors)"]["permissions"], ["read:orgs/x/buckets/b0c3"])
        self.assertEqual(values["INFLUX_TOKEN"], self.operator, "operator token is kept")
        self.assertEqual(stat.S_IMODE(self.env.stat().st_mode), 0o600)
        for secret in (self.operator, values["INFLUX_TELEGRAF_TOKEN"], values["INFLUX_GRAFANA_TOKEN"]):
            self.assertNotIn(secret, result.stdout + result.stderr)

    def test_refuses_duplicates_without_rotate(self):
        self.run_script()
        before = self.env.read_text()
        result = self.run_script()
        self.assertEqual(result.returncode, 2)
        self.assertIn("--rotate", result.stderr)
        self.assertEqual(self.env.read_text(), before)

    def test_rotate_replaces_values_and_lists_old_ids(self):
        self.run_script()
        old = self.env_values()
        result = self.run_script("--rotate")
        self.assertEqual(result.returncode, 0, result.stderr)
        new = self.env_values()
        self.assertNotEqual(new["INFLUX_TELEGRAF_TOKEN"], old["INFLUX_TELEGRAF_TOKEN"])
        self.assertEqual(sum(1 for line in self.env.read_text().splitlines() if line.startswith("INFLUX_GRAFANA_TOKEN=")), 1)
        self.assertIn("delete the replaced tokens", result.stdout)

    def test_dry_run_changes_nothing(self):
        before = self.env.read_text()
        result = self.run_script("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Would create", result.stdout)
        self.assertEqual(self.env.read_text(), before)
        self.assertEqual(json.loads(self.state.read_text())["auths"], [])

    def test_operator_token_not_leaked_on_error(self):
        self.state.write_text(json.dumps({"operator": "other", "auths": [], "buckets": []}))
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(self.operator, result.stdout + result.stderr)

    def test_project_name_is_passed_to_compose(self):
        self.run_script("--project-name", "coil-checkin2", "--dry-run")
        log = Path(str(self.state) + ".log").read_text()
        self.assertIn("-p coil-checkin2", log)


if __name__ == "__main__":
    unittest.main()
