"""Offline tests for wol_relay.Relay: contract acceptance tests 1-7 plus validation and persistence.

Tests 8 (broker ACL) and 9 (Last Will) need a broker; see wol-relay/README.md.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wol_relay as w  # noqa: E402

T0 = datetime(2026, 11, 25, 8, 30, 0, 123000, tzinfo=timezone.utc)
PC = w.Target("SZCZ-PC-01", "AA:BB:CC:DD:EE:01", "192.0.2.10", "tcp:3389")


class Clock:
    def __init__(self, start=T0):
        self.now = start

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += timedelta(seconds=seconds)


class Machine:
    """Simulated computer: turns on `boot_seconds` after the first magic packet (None = never)."""

    def __init__(self, clock, on=False, boot_seconds=40.0):
        self.clock, self.on, self.boot_seconds = clock, on, boot_seconds
        self.packets, self.first_packet = [], None

    def send(self, target):
        self.packets.append(self.clock())
        self.first_packet = self.first_packet or self.clock()

    def check(self, target):
        if self.on:
            return True
        return (self.boot_seconds is not None and self.first_packet is not None
                and self.clock() - self.first_packet >= timedelta(seconds=self.boot_seconds))


def command(**override):
    body = {"schema_version": 1, "request_id": str(uuid.uuid4()), "action": "wake", "target_id": "SZCZ-PC-01",
            "requested_by": "darek", "requested_at_utc": w.fmt_ts(T0),
            "expires_at_utc": w.fmt_ts(T0 + timedelta(seconds=60))}
    body.update(override)
    return body


class RelayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "state.sqlite3")
        self.clock = Clock()
        self.machine = Machine(self.clock)
        self.published = []
        self.relay = self.make_relay()

    def tearDown(self):
        self.tmp.cleanup()

    def make_relay(self):
        return w.Relay("pl-wol-relay", {"SZCZ-PC-01": PC}, w.Store(self.db), self.published.append,
                       send_packet=self.machine.send, check=self.machine.check,
                       clock=self.clock, sleep=self.clock.sleep)

    def send(self, body):
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        return self.relay.handle(payload)

    def states(self):
        return [m["state"] for m in self.published]

    # Contract test 1
    def test_valid_command_target_off_reaches_reachable(self):
        final = self.send(command())
        self.assertEqual(self.states(), ["accepted", "packet_sent", "reachable"])
        self.assertTrue(final["final"])
        self.assertEqual(len(self.machine.packets), 3)
        self.assertEqual(self.machine.packets[1] - self.machine.packets[0], timedelta(seconds=1))
        self.assertGreaterEqual(final["wake_seconds"], 40.0)
        self.assertEqual(final["check"], "tcp:3389")
        self.assertEqual([m["final"] for m in self.published], [False, False, True])

    # Contract test 2
    def test_target_already_on_sends_no_packet(self):
        self.machine.on = True
        final = self.send(command())
        self.assertEqual(self.states(), ["accepted", "already_on"])
        self.assertEqual(self.machine.packets, [])
        self.assertIsNone(final["packet_sent_at_utc"])

    # Contract test 3
    def test_unknown_target_rejected(self):
        final = self.send(command(target_id="SZCZ-PC-99"))
        self.assertEqual((final["state"], final["reason"]), ("rejected", "unknown_target"))
        self.assertEqual(self.machine.packets, [])

    # Contract test 4
    def test_expired_command_rejected(self):
        self.clock.now = T0 + timedelta(seconds=61)
        final = self.send(command())
        self.assertEqual(final["reason"], "expired")
        self.assertEqual(self.machine.packets, [])

    # Contract test 5
    def test_duplicate_request_republishes_identical_final_result(self):
        body = command()
        first = self.send(body)
        packets = len(self.machine.packets)
        self.published.clear()
        second = self.send(body)
        self.assertEqual(second, first)
        self.assertEqual(self.published, [first])
        self.assertEqual(len(self.machine.packets), packets, "no second execution")

    def test_duplicate_survives_relay_restart(self):
        body = command()
        first = self.send(body)
        self.relay = self.make_relay()            # new process, same state database
        self.published.clear()
        self.assertEqual(self.send(body), first)
        self.assertEqual(len(self.machine.packets), 3)

    # Contract test 6
    def test_second_command_within_60_s_is_rate_limited(self):
        self.send(command())
        self.clock.now = T0 + timedelta(seconds=10)
        late_ok = command(requested_at_utc=w.fmt_ts(self.clock.now),
                          expires_at_utc=w.fmt_ts(self.clock.now + timedelta(seconds=60)))
        self.clock.now = T0 + timedelta(seconds=50)   # 50 s after the first packet
        final = self.send(late_ok)
        self.assertEqual(final["reason"], "rate_limited")

    def test_rate_limit_survives_restart(self):
        self.send(command())
        self.relay = self.make_relay()
        self.clock.now = T0 + timedelta(seconds=45)
        body = command(requested_at_utc=w.fmt_ts(self.clock.now),
                       expires_at_utc=w.fmt_ts(self.clock.now + timedelta(seconds=60)))
        self.assertEqual(self.send(body)["reason"], "rate_limited")

    def test_busy_target_is_rate_limited(self):
        self.relay.busy.add("SZCZ-PC-01")
        self.assertEqual(self.send(command())["reason"], "rate_limited")

    # Contract test 7
    def test_unplugged_target_times_out_after_180_s(self):
        self.machine.boot_seconds = None
        final = self.send(command())
        self.assertEqual(self.states(), ["accepted", "packet_sent", "unreachable_timeout"])
        self.assertIsNone(final["reachable_at_utc"])
        sent = w.parse_ts(final["packet_sent_at_utc"])
        self.assertGreaterEqual(self.clock.now - sent, timedelta(seconds=180))

    # Validation order and shapes (contract section 7, rule 2)
    def test_malformed_variants(self):
        cases = [b"not json", json.dumps([1]).encode(), command(schema_version=2), command(schema_version=True),
                 command(request_id="not-a-uuid"), command(request_id=str(uuid.uuid1())),
                 command(requested_by="darek@example.org"), command(requested_at_utc="2026-11-25T08:30:00Z"),
                 command(expires_at_utc=w.fmt_ts(T0 + timedelta(seconds=600))), dict(command(), mac="AA:BB")]
        for case in cases:
            with self.subTest(case=case):
                self.published.clear()
                final = self.send(case)
                self.assertEqual((final["state"], final["reason"]), ("rejected", "malformed"))
        self.assertEqual(self.machine.packets, [])

    def test_malformed_without_readable_id_reports_null(self):
        self.assertIsNone(self.send(b"{}")["request_id"])

    def test_unsupported_action_checked_before_expiry(self):
        self.clock.now = T0 + timedelta(seconds=120)
        self.assertEqual(self.send(command(action="shutdown"))["reason"], "unsupported_action")

    def test_result_messages_match_contract_fields(self):
        self.send(command())
        expected = {"schema_version", "request_id", "relay_id", "target_id", "state", "final", "reason",
                    "packet_sent_at_utc", "reachable_at_utc", "wake_seconds", "check", "timestamp_utc"}
        for message in self.published:
            self.assertEqual(set(message), expected)
            self.assertRegex(message["timestamp_utc"], w.TS_RE)
            self.assertNotIn("AA:BB", json.dumps(message), "no MAC address in results")

    def test_unfinished_request_after_crash_is_not_re_executed(self):
        body = command()
        self.relay.store.begin(body["request_id"], T0)   # simulated crash mid-operation
        relay = self.make_relay()
        relay.recover()
        final = relay.handle(json.dumps(body).encode())
        self.assertEqual((final["state"], final["reason"]), ("rejected", "expired"))
        self.assertEqual(self.machine.packets, [])

    def test_prune_keeps_recent_results(self):
        store = w.Store(self.db)
        store.begin("old", T0 - timedelta(hours=25))
        store.finish("old", {"x": 1})
        store.begin("new", T0)
        store.finish("new", {"x": 2})
        self.assertEqual(store.prune(T0), 1)
        self.assertEqual(store.get("new"), (True, {"x": 2}))

    def test_magic_packet_layout(self):
        packet = w.magic_packet("aa-bb-cc-dd-ee-01")
        self.assertEqual(len(packet), 102)
        self.assertEqual(packet[:6], b"\xff" * 6)
        self.assertEqual(packet[6:12], bytes.fromhex("aabbccddee01"))
        with self.assertRaises(ValueError):
            w.magic_packet("AA:BB")


if __name__ == "__main__":
    unittest.main()
