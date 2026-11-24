"""Static check of published ports in deployment/contabo/compose.yml (IPv4 and IPv6 exposure).

DOCKER-USER only filters IPv4 traffic that Docker forwards. A port published without a host IP also
gets a docker-proxy listener on [::], whose IPv6 traffic never passes DOCKER-USER. Binding every
port to the WireGuard address or loopback is therefore the real control; this test enforces it.
Needs `docker compose`; skipped otherwise.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import unittest
from pathlib import Path

COMPOSE = Path(__file__).resolve().parents[2] / "deployment" / "contabo" / "compose.yml"
ALLOWED_HOSTS = {"10.66.0.1", "127.0.0.1"}


@unittest.skipUnless(shutil.which("docker"), "docker compose not available")
class ComposeBindingsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = dict(os.environ, INFLUX_ADMIN_USER="ci", GRAFANA_ALERT_EMAIL="ci@example.invalid")
        for name in ("INFLUX_ADMIN_PASSWORD", "INFLUX_TOKEN", "MQTT_TELEGRAF_PASSWORD", "GRAFANA_ADMIN_PASSWORD"):
            env[name] = secrets.token_hex(16)
        result = subprocess.run(["docker", "compose", "-f", str(COMPOSE), "config", "--format", "json"],
                                capture_output=True, text=True, env=env)
        if result.returncode != 0:
            raise unittest.SkipTest(f"docker compose config failed: {result.stderr[:200]}")
        cls.model = json.loads(result.stdout)

    def ports(self):
        for name, service in self.model["services"].items():
            for port in service.get("ports", []):
                yield name, port

    def test_every_published_port_has_a_specific_host_ip(self):
        for name, port in self.ports():
            with self.subTest(service=name, port=port.get("published")):
                self.assertIn(port.get("host_ip", ""), ALLOWED_HOSTS,
                              "published without a VPN/loopback host IP: reachable on 0.0.0.0 and [::]")

    def test_influxdb_is_not_published(self):
        self.assertEqual(self.model["services"]["influxdb"].get("ports", []), [])

    def test_vpn_ports_match_the_firewall_allow_list(self):
        firewall = (COMPOSE.parents[2] / "vpn" / "firewall" / "docker-user.sh").read_text(encoding="utf-8")
        for name, port in self.ports():
            if port.get("host_ip") == "10.66.0.1":
                with self.subTest(service=name):
                    self.assertRegex(firewall, rf"\b{port['published']}\b",
                                     "VPN-published port missing from docker-user.sh")


if __name__ == "__main__":
    unittest.main()
