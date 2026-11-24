"""Runs the Starlark processor from deployment/contabo/telegraf.conf under Python.

The script is kept inside the Starlark/Python common subset, so its logic (registry check, delay)
can be tested without a Telegraf binary. Telegraf-specific modules (time.star, logging.star) are
replaced by small stand-ins. This does not prove Telegraf parses the config; a Telegraf run does.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

CONF = Path(__file__).resolve().parents[2] / "deployment" / "contabo" / "telegraf.conf"
NOW_NS = 1_763_571_000_000_000_000


def load_processor():
    text = CONF.read_text(encoding="utf-8")
    match = re.search(r"\[\[processors\.starlark\]\].*?source = '''\n(.*?)'''", text, re.S)
    if not match:
        raise AssertionError("starlark processor not found in telegraf.conf")
    source = "\n".join(line for line in match.group(1).splitlines() if not line.startswith("load("))
    warnings = []

    class _Now:
        unix_nano = NOW_NS

    class _Time:
        @staticmethod
        def now():
            return _Now()

    class _Log:
        @staticmethod
        def warn(message):
            warnings.append(message)

    namespace = {"time": _Time, "log": _Log}
    exec(compile(source, str(CONF), "exec"), namespace)
    return namespace["apply"], warnings


class Metric:
    def __init__(self, sensor_id, city, sensor_type, topic, age_ms=250):
        self.tags = {"sensor_id": sensor_id, "city": city, "sensor_type": sensor_type, "topic": topic}
        self.fields = {"value": 1.0, "seq": 1}
        self.time = NOW_NS - age_ms * 1_000_000


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.apply, self.warnings = load_processor()

    def test_all_six_registered_sensors_pass_with_delay(self):
        rows = [("MATI-TEMP-01", "Mati", "mati", "temperature"), ("MATI-HUM-01", "Mati", "mati", "humidity"),
                ("MATI-AIR-01", "Mati", "mati", "airquality"), ("SZCZ-TEMP-01", "Szczecin", "szczecin", "temperature"),
                ("SZCZ-HUM-01", "Szczecin", "szczecin", "humidity"), ("SZCZ-AIR-01", "Szczecin", "szczecin", "airquality")]
        for sensor_id, city, topic_city, sensor_type in rows:
            with self.subTest(sensor_id=sensor_id):
                metric = Metric(sensor_id, city, sensor_type, f"coil/{topic_city}/{sensor_type}/{sensor_id}")
                out = self.apply(metric)
                self.assertIs(out, metric)
                self.assertAlmostEqual(out.fields["delay_ms"], 250.0)
        self.assertEqual(self.warnings, [])

    def test_unregistered_sensor_dropped(self):
        metric = Metric("SZCZ-TEMP-99", "Szczecin", "temperature", "coil/szczecin/temperature/SZCZ-TEMP-99")
        self.assertIsNone(self.apply(metric))
        self.assertIn("unregistered", self.warnings[0])

    def test_payload_sensor_id_must_match_topic(self):
        # A valid SZCZ-HUM-01 account publishing on its own topic but claiming to be SZCZ-TEMP-01.
        metric = Metric("SZCZ-TEMP-01", "Szczecin", "temperature", "coil/szczecin/humidity/SZCZ-HUM-01")
        self.assertIsNone(self.apply(metric))

    def test_city_and_type_must_match_registry(self):
        self.assertIsNone(self.apply(Metric("MATI-TEMP-01", "Szczecin", "temperature", "coil/mati/temperature/MATI-TEMP-01")))
        self.assertIsNone(self.apply(Metric("MATI-TEMP-01", "Mati", "humidity", "coil/mati/temperature/MATI-TEMP-01")))
        self.assertIsNone(self.apply(Metric("MATI-TEMP-01", "mati", "temperature", "coil/mati/temperature/MATI-TEMP-01")))

    def test_missing_tags_dropped(self):
        metric = Metric("MATI-AIR-01", "Mati", "airquality", "coil/mati/airquality/MATI-AIR-01")
        del metric.tags["topic"]
        self.assertIsNone(self.apply(metric))

    def test_registry_matches_data_design(self):
        doc = (CONF.parents[2] / "documents" / "data-design.md").read_text(encoding="utf-8")
        documented = set(re.findall(r"\| `((?:MATI|SZCZ)-[A-Z]+-\d{2})` \|", doc))
        conf = CONF.read_text(encoding="utf-8")
        configured = set(re.findall(r'"((?:MATI|SZCZ)-[A-Z]+-\d{2})": \(', conf))
        self.assertEqual(configured, documented)


if __name__ == "__main__":
    unittest.main()
