#!/usr/bin/env python3
"""Offline contract tests for projectd_simulator.py (no broker needed).

Run from the repository root:  python -m unittest discover -s simulators/tests -v
"""
import json
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import projectd_simulator as sim  # noqa: E402

ALL_SENSORS = [
    ("Mati", "temperature", "MATI-TEMP-01"),
    ("Mati", "humidity", "MATI-HUM-01"),
    ("Mati", "airquality", "MATI-AIR-01"),
    ("Szczecin", "temperature", "SZCZ-TEMP-01"),
    ("Szczecin", "humidity", "SZCZ-HUM-01"),
    ("Szczecin", "airquality", "SZCZ-AIR-01"),
]


class ContractTests(unittest.TestCase):
    def test_canonical_examples_from_data_design_are_valid(self):
        example = json.loads(
            '{"schema_version":1,"sensor_id":"SZCZ-TEMP-01","city":"Szczecin","sensor_type":"temperature",'
            '"timestamp_utc":"2026-11-10T08:30:00.123Z","seq":42,"value":6.4,"unit":"C","simulated":true}'
        )
        self.assertEqual(sim.validate_reading("coil/szczecin/temperature/SZCZ-TEMP-01", example), [])

    def test_every_sensor_produces_a_valid_reading_and_topic(self):
        for city, sensor_type, sensor_id in ALL_SENSORS:
            walk = sim.RandomWalk(city, sensor_type, random.Random(1))
            reading = sim.build_reading(city, sensor_type, sensor_id, 0, walk.next())
            topic = sim.reading_topic(city, sensor_type, sensor_id)
            self.assertEqual(sim.validate_reading(topic, reading), [], sensor_id)
            # Round-trip through JSON keeps the float type (decimal point is always written).
            decoded = json.loads(json.dumps(reading))
            self.assertIsInstance(decoded["value"], float)
            self.assertEqual(sim.validate_reading(topic, decoded), [])

    def test_whole_number_values_are_still_serialised_as_floats(self):
        reading = sim.build_reading("Szczecin", "temperature", "SZCZ-TEMP-01", 0, 6)
        self.assertIn('"value": 6.0', json.dumps(reading))

    def test_validator_rejects_contract_violations(self):
        good = sim.build_reading("Szczecin", "humidity", "SZCZ-HUM-01", 3, 80.0)
        topic = "coil/szczecin/humidity/SZCZ-HUM-01"
        cases = {
            "wrong topic": ("coil/mati/humidity/SZCZ-HUM-01", {}),
            "wrong unit": (topic, {"unit": "%"}),
            "integer value": (topic, {"value": 80}),
            "second-precision timestamp": (topic, {"timestamp_utc": "2026-11-10T08:30:00Z"}),
            "negative seq": (topic, {"seq": -1}),
            "not simulated": (topic, {"simulated": False}),
            "wrong city for id": (topic, {"city": "Mati"}),
            "nan value": (topic, {"value": float("nan")}),
        }
        for name, (case_topic, change) in cases.items():
            reading = {**good, **change}
            self.assertNotEqual(sim.validate_reading(case_topic, reading), [], name)
        extra = {**good, "extra": 1}
        self.assertNotEqual(sim.validate_reading(topic, extra), [])

    def test_random_walk_stays_in_range_and_respects_max_step(self):
        for city, sensor_type, _ in ALL_SENSORS:
            low, high, step = sim.NORMAL[(city, sensor_type)]
            walk = sim.RandomWalk(city, sensor_type, random.Random(7))
            previous = walk.value
            self.assertTrue(low <= previous <= high)
            for _ in range(20000):
                value = walk.next()
                self.assertTrue(low <= value <= high, (city, sensor_type, value))
                self.assertLessEqual(abs(value - previous), step + 1e-9, (city, sensor_type))
                self.assertEqual(value, round(value, 1))
                previous = value

    def test_normal_range_never_crosses_alert_thresholds(self):
        for key, (low, high, _) in sim.NORMAL.items():
            below, above = sim.THRESHOLDS[key]
            if below is not None:
                self.assertGreater(low, below, key)
            self.assertLess(high, above, key)

    def test_anomaly_crosses_threshold_for_every_sensor(self):
        for city, sensor_type, _ in ALL_SENSORS:
            _, above = sim.THRESHOLDS[(city, sensor_type)]
            self.assertGreater(sim.anomaly_value(city, sensor_type), above)

    def test_password_variable_names_match_gen_passwd(self):
        self.assertEqual(sim.password_env_name("SZCZ-TEMP-01"), "MQTT_SZCZ_TEMP_01_PASSWORD")
        self.assertEqual(sim.password_env_name("MATI-AIR-01"), "MQTT_MATI_AIR_01_PASSWORD")

    def test_status_topic(self):
        self.assertEqual(sim.status_topic("Szczecin", "SZCZ-AIR-01"), "coil/szczecin/status/SZCZ-AIR-01")


if __name__ == "__main__":
    unittest.main()
