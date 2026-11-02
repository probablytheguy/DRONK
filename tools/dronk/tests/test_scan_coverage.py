"""Scan coverage telemetry reaches the dashboard through the real bridge."""
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from test_support import IsolatedConfigTestCase

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import command_center as cc

PROCESSOR = cc.ROOT / "linux-bridge/build/install/linux-bridge/bin/linux-bridge"
FIXTURE = cc.ROOT / "linux-bridge/src/test/resources/dronk/observation.json"
RECEPTION = cc.ROOT / "linux-bridge/src/test/resources/dronk/reception.json"
HOP = [6, 149, 1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 36, 40, 44, 48, 153, 157, 161, 165]


def hopping_status(current, seq):
    """A C5-0.3-scan status record as SUSPECTRUM emits it."""
    record = json.loads(FIXTURE.read_text())
    for key in ("transport", "rssi_dbm", "transmitter", "message_counter", "association", "authenticated",
                "basic_id", "aircraft", "location_timestamp_seconds_after_hour", "operator", "received_uptime_us",
                "odid_protocol_version"):
        record.pop(key, None)
    record.update(type="status", state="monitoring", seq=seq, uptime_us=record["uptime_us"] + seq,
                  channel=current, frequency_mhz=2407 + 5 * current if current <= 11 else 5000 + 5 * current,
                  counters={"rf_frames": 40, "malformed_frames": 0},
                  scan={"mode": "hop", "dwell_ms": 250, "hot_ttl_s": 60, "current": current, "on_channel_ms": 90,
                        "switches": 12, "tune_errors": 0, "transition_drops": 3, "switch_us_last": 400, "switch_us_max": 800,
                        "channels": [[ch, 250, 2, 1 if ch == 11 else 0, 1 if ch == 11 else 0, 4 if ch == 11 else None,
                                      1 if ch == 11 else 0] for ch in HOP]})
    return (json.dumps(record) + "\n").encode()


class ScanCoverageTests(IsolatedConfigTestCase):
    def wait_for(self, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate(): return
            time.sleep(.05)
        self.fail("Timed out waiting for scan telemetry")

    def scan(self, state):
        receivers = state.snapshot()["receivers"]
        return receivers[0].get("scan") if receivers else None

    def test_capture_pty_hopping_status_reaches_receiver_card(self):
        master, slave = os.openpty()
        self.addCleanup(os.close, master); self.addCleanup(os.close, slave)
        state = cc.State()
        control = cc.HostController(state, dict(demo=False, devices={"front": os.ttyname(slave)}, expected={},
                                                processor=str(PROCESSOR), forward_live=False, duration=0, mode="CAPTURE"))
        self.addCleanup(control.stop)
        control.start()
        self.wait_for(lambda: any(r.get("connected") for r in state.snapshot()["receivers"]))
        os.write(master, hopping_status(6, 1))
        self.wait_for(lambda: self.scan(state) is not None)
        os.write(master, hopping_status(36, 2))
        self.wait_for(lambda: self.scan(state)["current"] == 36)
        scan, receiver = self.scan(state), state.snapshot()["receivers"][0]
        self.assertEqual((scan["mode"], receiver["channel"], receiver["rejected"]), ("hop", 36, 0))
        self.assertEqual([c["channel"] for c in scan["channels"]], HOP)
        hot = [c for c in scan["channels"] if c["hot"]]
        self.assertEqual([(c["channel"], c["decoded"], c["last_decode_age_s"]) for c in hot], [(11, 1, 4)])
        self.assertEqual((state.snapshot()["operation_mode"], state.snapshot()["tak"]["state"]), ("CAPTURE", "DISABLED"))
        self.assertEqual(state.snapshot()["detection"]["state"], "CLEAR")  # scan telemetry is not a detection

    def test_capture_pty_firmware_reception_is_detection_without_track_or_cot(self):
        master, slave = os.openpty()
        self.addCleanup(os.close, master); self.addCleanup(os.close, slave)
        state = cc.State()
        control = cc.HostController(state, dict(demo=False, devices={"front": os.ttyname(slave)}, expected={},
                                                processor=str(PROCESSOR), forward_live=False, duration=0, mode="CAPTURE"))
        self.addCleanup(control.stop)
        control.start()
        self.wait_for(lambda: any(r.get("connected") for r in state.snapshot()["receivers"]))
        os.write(master, RECEPTION.read_bytes())  # exact firmware reception format (cross-checked in firmware tests)
        self.wait_for(lambda: state.snapshot()["detection"]["state"] == "DETECTED")
        snapshot = state.snapshot()
        self.assertEqual((snapshot["detection"]["tracked_count"], snapshot["detection"]["untracked_count"]), (0, 1))
        self.assertEqual(snapshot["detection"]["untracked"][0]["id"], "SYNTH-C5-TEST")
        self.assertEqual((snapshot["aircraft"], snapshot["history"], snapshot["receivers"][0]["rejected"]), ([], [], 0))
        self.assertEqual((snapshot["tak"]["state"], snapshot["tak"]["written"]), ("DISABLED", 0))

    def test_demo_reports_synthetic_hopping_coverage(self):
        state = cc.State(True)
        control = cc.HostController(state, dict(demo=True, devices={}, expected={}, processor=str(PROCESSOR),
                                                forward_live=False, duration=0))
        self.addCleanup(control.stop)
        control.start()
        self.wait_for(lambda: self.scan(state) is not None)
        scan = self.scan(state)
        self.assertEqual((scan["mode"], scan["current"], len(scan["channels"])), ("hop", 149, 20))
        self.assertTrue(next(c for c in scan["channels"] if c["channel"] == 149)["hot"])
        self.wait_for(lambda: state.snapshot()["detection"]["untracked_count"] == 1)
        self.assertEqual(state.snapshot()["detection"]["untracked"][0]["id"], "SYNTH-NOFIX-03")
