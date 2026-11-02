"""Drone detection panel: decoded Remote ID tracks only, expiry, disconnect, restart."""
from datetime import datetime, timezone
from pathlib import Path
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from test_support import IsolatedConfigTestCase

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import command_center as cc

T0 = 1_800_000_000.0


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


class PanelStateCase(IsolatedConfigTestCase):
    """Shared clock-controlled State fixture; holds no tests itself."""
    def setUp(self):
        super().setUp()
        self.now = T0
        clock = patch.object(cc.time, "time", side_effect=lambda: self.now)
        clock.start(); self.addCleanup(clock.stop)
        self.state = cc.State()
        self.state.host["state"] = "RUNNING"
        self.state.apply({"kind": "ready", "mode": "CAPTURE", "stale_seconds": 60})

    def receiver(self, stream="front", connected=True, seen=None, rf_frames=0, candidates=0):
        self.state.apply({"kind": "receiver", "receiver": {"stream": stream, "connected": connected,
            "last_seen": iso(self.now if seen is None else seen), "counters": {"rf_frames": rf_frames, "candidate_frames": candidates}}})

    def observation(self, uid="uid-1", kind="observation", lifetime=60):
        self.state.apply({"kind": kind, "expires_at": iso(self.now + lifetime), "reason": "test",
                          "aircraft": {"uid": uid, "id": "SYNTH-PANEL", "synthetic": False, "received_at": iso(self.now)}})

    def panel(self):
        return self.state.snapshot()["detection"]



class DetectionPanelTests(PanelStateCase):
    def test_detection_counts_tracked_remote_id_only(self):
        self.receiver(rf_frames=5000, candidates=40)
        self.observation(kind="reception")  # decoded but not tracked: not an active drone
        self.assertEqual((self.panel()["state"], self.panel()["active_count"]), ("CLEAR", 0))
        self.observation("uid-1"); self.observation("uid-2")
        self.now += 7
        self.receiver()
        panel = self.panel()
        self.assertEqual((panel["state"], panel["active_count"], panel["seconds_since_last"]), ("DETECTED", 2, 7))
        self.assertEqual((panel["receivers_online"], panel["receivers_total"], panel["stale_seconds"]), (1, 1, 60))

    def test_rf_and_candidate_counters_never_trigger_detection(self):
        self.receiver(rf_frames=10**6, candidates=10**4)
        self.assertEqual(self.panel()["state"], "CLEAR")
        self.assertIsNone(self.panel()["seconds_since_last"])

    def test_expiry_returns_to_clear_and_keeps_last_detection_age(self):
        self.receiver(); self.observation(lifetime=60)
        self.now += 59; self.receiver()
        self.assertEqual(self.panel()["state"], "DETECTED")
        self.now += 2; self.receiver()
        panel = self.panel()
        self.assertEqual((panel["state"], panel["active_count"], panel["seconds_since_last"]), ("CLEAR", 0, 61))

    def test_tracks_event_prunes_active_detections(self):
        self.receiver(); self.observation("uid-1"); self.observation("uid-2")
        self.state.apply({"kind": "tracks", "uids": ["uid-2"]})
        self.assertEqual(self.panel()["active_count"], 1)
        self.state.apply({"kind": "tracks", "uids": []})
        self.assertEqual(self.panel()["state"], "CLEAR")

    def test_receiver_disconnect_and_stale_telemetry_are_offline(self):
        self.receiver(); self.observation()
        self.receiver(connected=False)
        self.assertEqual((self.panel()["state"], self.panel()["reason"]), ("OFFLINE", "Receiver disconnected"))
        self.receiver()
        self.assertEqual(self.panel()["state"], "DETECTED")
        self.now += cc.TELEMETRY_STALE_SECONDS
        self.assertEqual((self.panel()["state"], self.panel()["reason"]), ("OFFLINE", "Receiver telemetry stale"))

    def test_one_of_two_receivers_offline_still_reports(self):
        self.receiver("front"); self.receiver("rear", connected=False)
        panel = self.panel()
        self.assertEqual((panel["state"], panel["receivers_online"], panel["receivers_total"]), ("CLEAR", 1, 2))

    def test_stopped_or_never_started_host_is_offline(self):
        self.receiver(); self.observation()
        self.state.host["state"] = "STOPPED"
        self.assertEqual((self.panel()["state"], self.panel()["reason"]), ("OFFLINE", "Host monitoring stopped"))
        fresh = cc.State()
        self.assertEqual(fresh.snapshot()["detection"]["state"], "OFFLINE")

    def test_restart_clears_previous_session_tracks(self):
        self.receiver(); self.observation()
        self.state.new_session()
        self.receiver()
        panel = self.panel()
        self.assertEqual((panel["state"], panel["active_count"], panel["seconds_since_last"]), ("CLEAR", 0, None))
        self.assertEqual(len(self.state.snapshot()["history"]), 1)  # history/export are kept


class ReceptionVersusTrackingTests(PanelStateCase):
    """Phase 2: valid decoded Remote ID without ID/position is a detection, never a track."""
    def rid_reception(self, transmitter="02:00:00:00:00:09", reason="no_valid_position", synthetic=False):
        self.state.apply({"kind": "rid_reception", "reception": {
            "transmitter": transmitter, "reason": reason, "channel": 6, "transport": "wifi_beacon", "synthetic": synthetic,
            "basic_ids": [{"id_type": 1, "ua_type": 2, "value": "SYNTH-NOFIX"}] if reason != "no_basic_id" else [],
            "received_at": iso(self.now), "messages": ["basic_id", "location"], "aircraft_location": "unavailable"}})

    def tracked(self, transmitter, uid="uid-t"):
        self.state.apply({"kind": "observation", "expires_at": iso(self.now + 60), "reason": "test",
                          "aircraft": {"uid": uid, "id": "SYNTH-T", "synthetic": False, "received_at": iso(self.now),
                                       "rf": {"transmitter": transmitter}}})

    def test_untrackable_remote_id_is_detected_not_tracked(self):
        self.receiver(); self.rid_reception()
        self.now += 4; self.receiver()
        panel = self.panel()
        self.assertEqual((panel["state"], panel["active_count"], panel["tracked_count"], panel["untracked_count"]), ("DETECTED", 1, 0, 1))
        self.assertEqual(panel["untracked"], [dict(transmitter="02:00:00:00:00:09", reason="no_valid_position", channel=6,
                                                  transport="wifi_beacon", id="SYNTH-NOFIX", seconds_ago=4)])
        self.assertEqual(panel["seconds_since_last"], 4)
        self.assertEqual(self.state.snapshot()["aircraft"], [])
        self.assertEqual(self.state.snapshot()["history"], [])  # export holds tracked observations only

    def test_reception_expires_after_tracker_stale_timeout(self):
        self.receiver(); self.rid_reception()
        self.now += 59; self.receiver()
        self.assertEqual(self.panel()["state"], "DETECTED")
        self.now += 2; self.receiver()
        self.assertEqual((self.panel()["state"], self.panel()["seconds_since_last"]), ("CLEAR", 61))
        self.state.apply({"kind": "ready", "stale_seconds": 30})
        self.rid_reception("02:00:00:00:00:0a")
        self.now += 31; self.receiver()
        self.assertEqual(self.panel()["state"], "CLEAR")

    def test_tracked_transmitter_counts_once(self):
        self.receiver(); self.rid_reception("02:00:00:00:00:01"); self.tracked("02:00:00:00:00:01")
        self.assertEqual((self.panel()["active_count"], self.panel()["tracked_count"], self.panel()["untracked_count"]), (1, 1, 0))
        self.rid_reception("02:00:00:00:00:02", reason="no_basic_id")
        self.assertEqual((self.panel()["active_count"], self.panel()["untracked"][0]["id"]), (2, None))

    def test_counters_alone_never_detect(self):
        self.state.apply({"kind": "receiver", "receiver": {"stream": "front", "connected": True, "last_seen": iso(self.now),
            "counters": {"rf_frames": 9999, "candidate_frames": 500, "malformed_frames": 400, "decode_failures": 300,
                         "decoded_packs": 200, "rid_receptions": 100}}})
        self.assertEqual(self.panel()["state"], "CLEAR")

    def test_disconnect_restart_and_isolation(self):
        self.receiver(); self.rid_reception()
        self.receiver(connected=False)
        self.assertEqual(self.panel()["state"], "OFFLINE")
        self.state.new_session(); self.receiver()
        self.assertEqual((self.panel()["state"], self.panel()["untracked"]), ("CLEAR", []))
        with self.assertRaises(ValueError):
            self.rid_reception(synthetic=True)


class DetectionPanelProcessTests(IsolatedConfigTestCase):
    def wait_for(self, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate(): return
            time.sleep(.05)
        self.fail("Timed out waiting for detection state")

    def test_demo_detect_stop_and_restart(self):
        state = cc.State(True)
        control = cc.HostController(state, dict(demo=True, devices={}, expected={}, forward_live=False, duration=0,
            processor=str(cc.ROOT / "linux-bridge/build/install/linux-bridge/bin/linux-bridge")))
        self.addCleanup(control.stop)
        panel = lambda: state.snapshot()["detection"]
        control.start()
        self.wait_for(lambda: panel()["state"] == "DETECTED")
        self.assertTrue(panel()["synthetic"])
        self.assertEqual(panel()["stale_seconds"], 60)
        control.stop()
        self.assertEqual((panel()["state"], panel()["reason"]), ("OFFLINE", "Host monitoring stopped"))
        control.start()
        self.assertEqual((panel()["active_count"], panel()["seconds_since_last"]), (0, None))
        self.wait_for(lambda: panel()["state"] == "DETECTED")
