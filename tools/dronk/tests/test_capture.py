"""Phase 0 capture: append-only JSONL, session boundaries, restarts, duplicates, malformed input, interruption."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from test_support import IsolatedConfigTestCase

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import capture_log as cl
import command_center as cc
import configuration as cfg
from serial_sources import envelope, DATA, CONNECTED, DISCONNECTED
from test_detection_panel import PanelStateCase, iso
from test_scan_coverage import PROCESSOR, RECEPTION, hopping_status

GiB = 2**30


def disk(free):
    return lambda _path: SimpleNamespace(free=free)


def records(path, type_=None):
    found = cl.read_capture(path)["records"]
    return [r for r in found if type_ is None or r["type"] == type_]


class CaptureLogTests(IsolatedConfigTestCase):
    def setUp(self):
        super().setUp()
        self.dir = self.test_home / "captures"

    def log(self, **kw):
        log = cl.CaptureLog(self.dir, "CAPTURE", ["front", "rear"], disk_usage=kw.pop("disk_usage", disk(10 * GiB)), **kw)
        self.addCleanup(log.close, "test")
        return log

    def test_header_end_and_private_unique_file(self):
        log = self.log()
        log.close("requested")
        found = cl.read_capture(log.path)
        self.assertTrue(found["complete"] and found["consistent"])
        self.assertFalse(found["truncated_tail"])
        head, end = found["records"][0], found["records"][-1]
        self.assertEqual((head["schema"], head["version"], head["mode"], head["synthetic"], head["streams"]),
                         ("dronk.capture", 1, "CAPTURE", False, ["front", "rear"]))
        self.assertEqual((end["type"], end["reason"]), ("capture_end", "requested"))
        self.assertEqual([r["n"] for r in found["records"]], list(range(len(found["records"]))))
        self.assertEqual(stat.S_IMODE(log.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.dir.stat().st_mode), 0o700)
        self.assertRegex(log.path.name, r"^dronk-capture-\d{8}T\d{6}\.\d{3}Z-[0-9a-f]{8}\.jsonl$")

    def test_restart_never_overwrites_and_links_previous(self):
        first = self.log(); first.envelope(envelope(DATA, 0, b'{"a":1}\n')); first.close("requested")
        digest = hashlib.sha256(first.path.read_bytes()).hexdigest()
        second = self.log(); second.close("requested")
        self.assertNotEqual(first.path, second.path)
        self.assertEqual(hashlib.sha256(first.path.read_bytes()).hexdigest(), digest)
        self.assertEqual(records(second.path)[0]["previous_capture"], first.path.name)

    def test_name_collision_fails_instead_of_overwriting(self):
        fixed, real = cl.uuid.UUID("12345678-0000-4000-8000-000000000000"), cl.datetime
        with patch.object(cl.uuid, "uuid4", return_value=fixed), patch.object(cl, "datetime") as clock:
            clock.now.return_value = real(2026, 11, 2, 13, 39, 17, tzinfo=cl.timezone.utc)
            clock.fromtimestamp = real.fromtimestamp
            first = self.log(); first.close("requested")
            before = first.path.read_bytes()
            with self.assertRaises(cl.CaptureError):
                self.log()
        self.assertEqual(first.path.read_bytes(), before)

    def test_raw_lines_split_across_chunks_crlf_and_streams(self):
        log = self.log()
        log.envelope(envelope(DATA, 0, b'{"type":"hel'))
        log.envelope(envelope(DATA, 1, b"rear one\r\n"))
        log.envelope(envelope(DATA, 0, b'lo"}\r\n{"type":"status"}\nnext'))
        log.close("requested")
        raw = records(log.path, "rx_line")
        self.assertEqual([(r["stream"], r.get("line"), r.get("partial", False)) for r in raw],
                         [("rear", "rear one", False), ("front", '{"type":"hello"}', False), ("front", '{"type":"status"}', False),
                          ("front", "next", True)])
        self.assertTrue(all(r["layer"] == "raw_receiver" for r in raw))

    def test_malformed_input_is_recorded_not_fatal(self):
        log = self.log()
        log.envelope(envelope(DATA, 0, b"\xff\xfe garbage\n"))
        log.envelope(envelope(DATA, 0, b"x" * 5000 + b"tail\n" + b'{"ok":1}\n'))
        log.envelope(envelope(DATA, 0, b"y" * 4096))  # exactly MAX_LINE, then the newline in the next chunk
        log.envelope(envelope(DATA, 0, b"\n"))
        log.envelope(envelope(DATA, 9, b"no such stream\n"))
        log.close("requested")
        raw = records(log.path, "rx_line")
        self.assertEqual(cl.base64.b64decode(raw[0]["line_b64"]), b"\xff\xfe garbage")
        self.assertEqual((len(raw[1]["line"]), raw[1]["truncated"]), (cl.MAX_LINE, True))
        self.assertEqual(raw[2]["line"], '{"ok":1}')  # the rest of the overlong line was discarded, not split off
        self.assertEqual((len(raw[3]["line"]), raw[3].get("truncated", False)), (4096, False))
        self.assertEqual(len(raw), 4)

    def test_usb_events_count_disconnects_and_flush_partial_line(self):
        log = self.log()
        log.envelope(envelope(CONNECTED, 0))
        log.envelope(envelope(DATA, 0, b'{"half'))
        log.envelope(envelope(DISCONNECTED, 0))
        log.envelope(envelope(CONNECTED, 0))
        log.close("requested")
        found = records(log.path)
        kinds = [(r["type"], r.get("event"), r.get("partial")) for r in found[1:-1]]
        self.assertEqual(kinds, [("usb", "connected", None), ("rx_line", None, True), ("usb", "disconnected", None), ("usb", "connected", None)])
        self.assertEqual(found[-1]["usb_disconnects"], {"front": 1, "rear": 0})

    def test_bridge_layers_and_missing_gps_reception(self):
        log = self.log()
        reception = json.loads(Path(__file__).with_name("fixtures").joinpath("field-2026-11-02-avata-receptions.jsonl").read_text().splitlines()[0])
        log.bridge(reception)
        log.bridge({"kind": "tracks", "uids": []})
        log.bridge({"kind": "tak", "state": "DISABLED"}); log.bridge({"kind": "tak", "state": "DISABLED"})
        log.close("requested")
        bridge = records(log.path, "bridge")
        self.assertEqual([(b["layer"], b["event"]["kind"]) for b in bridge], [("decoded_remote_id", "rid_reception"), ("telemetry", "tak")])
        self.assertEqual(bridge[0]["event"]["reception"]["aircraft_location"], "unavailable")
        self.assertNotIn("position", bridge[0]["event"]["reception"])

    def receiver(self, log, session="s1", uptime_s=1, **counters):
        log.bridge({"kind": "receiver", "receiver": {"stream": "front", "connected": True, "session_id": session,
                    "reset_reason": 1, "uptime_us": int(uptime_s * 1e6), "counters": dict(dict(usb_output_drops=8469, malformed_frames=316,
                    queue_drops=0, rf_frames=457238), **counters)}})

    def test_unchanged_receiver_telemetry_written_once(self):
        log = self.log()
        self.receiver(log); self.receiver(log); self.receiver(log, rf_frames=1)
        log.close("requested")
        self.assertEqual(len([r for r in records(log.path, "bridge") if r["event"]["kind"] == "receiver"]), 2)

    def test_diagnostics_are_session_relative_and_track_receiver_reboot(self):
        log = self.log()
        self.receiver(log)                                   # lifetime totals become the baseline
        self.receiver(log, rf_frames=458000)                 # traffic only: no new diagnostics record
        self.receiver(log, malformed_frames=332, rf_frames=458458)
        self.receiver(log, session="s2", usb_output_drops=0, malformed_frames=0, rf_frames=10)
        status = log.status()
        log.close("requested")
        diag = records(log.path, "diagnostics")
        self.assertEqual([(d["session_id"], d["delta"]["malformed_frames"], d["delta"]["usb_output_drops"]) for d in diag],
                         [("s1", 0, 0), ("s1", 16, 0), ("s2", 0, 0)])
        self.assertEqual((diag[1]["delta"]["rf_frames"], diag[1]["lifetime"]["malformed_frames"]), (1220, 332))
        session, = records(log.path, "receiver_session")
        self.assertEqual((session["previous_session_id"], session["session_id"]), ("s1", "s2"))
        self.assertEqual(status["receivers"][0]["session_id"], "s2")
        self.assertEqual(records(log.path)[-1]["receivers"]["front"]["session_id"], "s2")

    def test_stale_buffered_status_on_connect_is_not_the_baseline(self):
        """2026-11-02 hardware: the receiver first delivered two statuses buffered 37 min earlier."""
        now = [100.0]
        log = self.log(clock=lambda: now[0])
        self.receiver(log, uptime_s=43459.5, malformed_frames=333, rf_frames=458500)
        self.receiver(log, uptime_s=43464.5, malformed_frames=334, rf_frames=458547)   # same burst, still stale
        now[0] += 4
        self.receiver(log, uptime_s=45716.7, usb_output_drops=8915, malformed_frames=350, rf_frames=490503)
        now[0] += 5
        self.receiver(log, uptime_s=45721.8, usb_output_drops=8915, malformed_frames=351, rf_frames=490562)
        status = log.status()["receivers"][0]["delta"]
        log.close("requested")
        diag = records(log.path, "diagnostics")
        self.assertEqual([(d["rebaselined"], d["delta"]["usb_output_drops"], d["delta"]["malformed_frames"]) for d in diag],
                         [(None, 0, 0), (None, 0, 1), ("stale_buffered_status", 0, 0), ("stale_buffered_status", 0, 1)])
        self.assertEqual((status["usb_output_drops"], status["malformed_frames"], status["rf_frames"]), (0, 1, 59))
        self.assertEqual(records(log.path, "receiver_session"), [])  # same boot: not a reboot

    def test_normal_status_cadence_keeps_baseline(self):
        now = [100.0]
        log = self.log(clock=lambda: now[0])
        for i in range(4):
            self.receiver(log, uptime_s=500 + 5 * i, usb_output_drops=8469 + i)
            now[0] += 5
        self.assertEqual(log.status()["receivers"][0]["delta"]["usb_output_drops"], 3)

    def test_host_closing_its_port_is_not_a_disconnect(self):
        log = self.log()
        log.envelope(envelope(CONNECTED, 0)); log.envelope(envelope(DISCONNECTED, 0))  # real unplug
        log.envelope(envelope(CONNECTED, 0))
        log.closing = True
        log.envelope(envelope(DISCONNECTED, 0))  # requested stop
        log.close("requested")
        self.assertEqual([r["event"] for r in records(log.path, "usb")], ["connected", "disconnected", "connected", "closed_by_host"])
        self.assertEqual(records(log.path)[-1]["usb_disconnects"], {"front": 1, "rear": 0})

    def test_disk_space_refused_at_start(self):
        with self.assertRaises(cl.CaptureError):
            cl.CaptureLog(self.dir, "CAPTURE", ["front"], disk_usage=disk(cl.MIN_FREE_BYTES - 1))
        self.assertEqual(list(self.dir.glob("*.jsonl")), [])

    def test_low_disk_suspends_and_keeps_end_marker(self):
        free = [10 * GiB]
        log = self.log(disk_usage=lambda _p: SimpleNamespace(free=free[0]))
        log.envelope(envelope(DATA, 0, b"before\n"))
        free[0] = 1
        log.check_disk()
        log.envelope(envelope(DATA, 0, b"after\n"))
        self.assertEqual(log.status()["state"], "SUSPENDED")
        log.close("requested")
        found = records(log.path)
        self.assertEqual([r["type"] for r in found], ["capture_start", "rx_line", "capture_suspended", "capture_end"])
        self.assertEqual((found[2]["reason"], found[3]["suspended"]), ("low_disk_space", "low_disk_space"))

    def test_file_size_cap_suspends(self):
        log = self.log(max_file_bytes=2000)
        for _ in range(20):
            log.envelope(envelope(DATA, 0, b"z" * 100 + b"\n"))
        log.close("requested")
        found = records(log.path)
        self.assertEqual(found[-2]["reason"], "max_file_size")
        self.assertLess(log.path.stat().st_size, 2600)

    def test_interrupted_capture_is_readable(self):
        log = self.log()
        log.envelope(envelope(DATA, 0, b'{"one":1}\n'))
        log.sync()
        os.write(log.fd, b'{"capture_id":"x","n":9,"type":"rx_li')  # power lost mid-write
        os.close(log.fd); log.fd = None
        found = cl.read_capture(log.path)
        self.assertEqual((found["complete"], found["truncated_tail"], found["malformed_lines"]), (False, True, 0))
        self.assertEqual([r["type"] for r in found["records"]], ["capture_start", "rx_line"])

    def test_reader_skips_corrupt_middle_line_and_flags_foreign_records(self):
        log = self.log(); log.close("requested")
        lines = log.path.read_bytes().split(b"\n")
        lines.insert(1, b"not json")
        lines.insert(1, b'{"capture_id":"other","n":1,"type":"rx_line"}')
        log.path.write_bytes(b"\n".join(lines))
        found = cl.read_capture(log.path)
        self.assertEqual((found["malformed_lines"], found["complete"], found["consistent"]), (1, True, False))


class CaptureConfigTests(IsolatedConfigTestCase):
    def args(self, **kw):
        return argparse.Namespace(**dict(dict(mode=None, demo=False, capture_dir=None, no_capture=False), **kw))

    def test_default_directory_is_private_state_outside_repository(self):
        result = cfg.resolve(self.args(), {"front": "/dev/DO_NOT_OPEN"}, {})
        self.assertEqual(result["capture_dir"], str(self.test_home / ".local/state/dronk/captures"))
        self.assertIsNone(cfg.resolve(self.args(no_capture=True), {"front": "/dev/DO_NOT_OPEN"}, {})["capture_dir"])

    def test_demo_captures_only_when_asked(self):
        self.assertIsNone(cfg.resolve(self.args(demo=True), {}, {})["capture_dir"])
        target = str(self.test_home / "demo-captures")
        self.assertEqual(cfg.resolve(self.args(demo=True, capture_dir=target), {}, {})["capture_dir"], target)

    def test_repository_and_relative_paths_rejected(self):
        for bad in (str(cc.ROOT / "captures"), "relative/captures"):
            with self.assertRaises(ValueError):
                cfg.resolve(self.args(capture_dir=bad), {"front": "/dev/DO_NOT_OPEN"}, {})


class DuplicateEventTests(PanelStateCase):
    def reception(self, session="a" * 32, seq="4"):
        event = {"kind": "rid_reception", "reception": {
            "stream": "front", "receiver_id": "front", "transmitter": "02:00:00:00:00:01", "transport": "wifi_beacon",
            "channel": 149, "frequency_mhz": 5745, "rssi_dbm": -50, "reason": "no_valid_position", "synthetic": False,
            "basic_ids": [{"id_type": 1, "ua_type": 2, "value": "SYNTH-DUP"}], "received_at": iso(self.now),
            "messages": ["basic_id", "location", "system"], "aircraft_location": "unavailable"}}
        if session is not None:
            event["reception"].update(session_id=session, seq=seq)
        self.state.apply(event)

    def count(self):
        return self.state.snapshot()["detections"][0]["observation_count"]

    def test_same_receiver_record_counted_once(self):
        self.reception(); self.reception()
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.state.snapshot()["logs"][0]["kind"], "duplicate")
        self.reception(seq="5"); self.reception(session="b" * 32)
        self.assertEqual(self.count(), 3)

    def test_older_events_without_provenance_still_count(self):
        self.reception(session=None); self.reception(session=None)
        self.assertEqual(self.count(), 2)

    def test_capture_status_is_shown_not_logged(self):
        self.state.apply({"kind": "capture", "file": "dronk-capture-x.jsonl", "state": "RECORDING", "records": 3})
        snap = self.state.snapshot()
        self.assertEqual(snap["capture"]["file"], "dronk-capture-x.jsonl")
        self.assertNotIn("capture", [entry["kind"] for entry in snap["logs"]])
        self.state.new_session()
        self.assertIsNone(self.state.snapshot()["capture"])


def status_with(seq, **counters):
    """Status in the same receiver session as the reception fixture (a stream stays bound to one session)."""
    record = json.loads(hopping_status(6, seq))
    fixture = json.loads(RECEPTION.read_text())
    record.update(receiver_id=fixture["receiver_id"], session_id=fixture["session_id"], uptime_us=fixture["uptime_us"] + (seq - 4) * 1000)
    record["counters"].update(counters)
    return (json.dumps(record) + "\n").encode()


class WorkerCaptureTests(IsolatedConfigTestCase):
    """Through the real worker and bridge with a pseudo-terminal as the receiver."""
    def setUp(self):
        super().setUp()
        self.dir = self.test_home / "captures"
        self.master, slave = os.openpty()
        self.addCleanup(os.close, self.master); self.addCleanup(os.close, slave)
        self.tty = os.ttyname(slave)

    def wait_for(self, predicate, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate(): return
            time.sleep(.05)
        self.fail("Timed out")

    def controller(self, capture_dir=None):
        state = cc.State()
        control = cc.HostController(state, dict(demo=False, devices={"front": self.tty}, expected={}, processor=str(PROCESSOR),
                                                forward_live=False, duration=0, mode="CAPTURE",
                                                capture_dir=str(capture_dir or self.dir)))
        self.addCleanup(control.stop)
        return state, control

    def files(self):
        return sorted(self.dir.glob("dronk-capture-*.jsonl"))

    def test_capture_records_raw_and_decoded_layers_with_provenance(self):
        state, control = self.controller()
        control.start()
        self.wait_for(lambda: any(r.get("connected") for r in state.snapshot()["receivers"]))
        os.write(self.master, b"\xff\xfeboot noise\n" + status_with(1, usb_output_drops=3, malformed_frames=0))
        os.write(self.master, RECEPTION.read_bytes() + RECEPTION.read_bytes())  # second is a receiver duplicate
        os.write(self.master, status_with(5, usb_output_drops=3, malformed_frames=5))
        self.wait_for(lambda: (state.snapshot()["capture"] or {}).get("receivers", [{}])[0].get("delta", {}).get("malformed_frames") == 5)
        snap = state.snapshot()
        self.assertEqual((snap["capture"]["state"], snap["detections"][0]["observation_count"]), ("RECORDING", 1))
        control.stop()
        path, = self.files()
        found = cl.read_capture(path)
        self.assertTrue(found["complete"] and found["consistent"])
        self.assertEqual(found["records"][-1]["reason"], "requested")
        raw = [r for r in found["records"] if r["type"] == "rx_line"]
        self.assertEqual(cl.base64.b64decode(raw[0]["line_b64"]), b"\xff\xfeboot noise")
        self.assertEqual(sum('"type": "reception"' in r.get("line", "") or '"type":"reception"' in r.get("line", "") for r in raw), 2)
        decoded = [r["event"] for r in found["records"] if r["type"] == "bridge" and r["layer"] == "decoded_remote_id"]
        self.assertEqual(len(decoded), 1)  # the hub dropped the duplicate; the raw layer still has both
        rec = decoded[0]["reception"]
        self.assertEqual((rec["session_id"], rec["seq"], rec["receiver_id"], rec["message_counter"]), ("a" * 32, "4", "front", 7))
        self.assertEqual((rec["aircraft_location"], rec["reason"]), ("unavailable", "no_valid_position"))
        diag = [r for r in found["records"] if r["type"] == "diagnostics"]
        self.assertEqual([d["delta"].get("malformed_frames") for d in diag], [0, 5])
        usb = [r["event"] for r in found["records"] if r["type"] == "usb"]
        self.assertEqual(usb, ["connected", "closed_by_host"])  # a requested stop is not a disconnect
        self.assertEqual(found["records"][-1]["usb_disconnects"], {"front": 0})
        self.assertEqual(state.snapshot()["capture"]["state"], "CLOSED")  # final status reaches the dashboard
        self.assertNotIn(self.tty, path.read_text())  # device paths stay out of captures

    def test_restart_creates_new_file_and_keeps_previous(self):
        state, control = self.controller()
        control.start(); self.wait_for(lambda: state.snapshot()["capture"] is not None); control.stop()
        first, = self.files()
        digest = hashlib.sha256(first.read_bytes()).hexdigest()
        control.start(); self.wait_for(lambda: state.snapshot()["capture"] is not None)
        control.stop()  # usually still during bridge startup: a cleanly ended capture either way
        files = self.files()
        self.assertEqual(len(files), 2)
        self.assertEqual(hashlib.sha256(first.read_bytes()).hexdigest(), digest)
        second, = [f for f in files if f != first]
        self.assertEqual(records(second)[0]["previous_capture"], first.name)
        self.assertEqual([(r["type"], r.get("reason")) for r in records(second)][-1], ("capture_end", "requested"))
        self.assertEqual(records(first)[-1]["reason"], "requested")

    def test_killed_worker_leaves_readable_interrupted_capture(self):
        state, control = self.controller()
        control.start()
        self.wait_for(lambda: any(r.get("connected") for r in state.snapshot()["receivers"]))
        os.write(self.master, RECEPTION.read_bytes())
        self.wait_for(lambda: state.snapshot()["detection"]["untracked_count"] == 1)
        time.sleep(2.5)  # past one periodic fsync
        os.killpg(control.child.pid, signal.SIGKILL)
        self.wait_for(lambda: state.snapshot()["host"]["state"] == "ERROR")
        path, = self.files()
        found = cl.read_capture(path)
        self.assertFalse(found["complete"])
        self.assertTrue(any(r["type"] == "bridge" and r["layer"] == "decoded_remote_id" for r in found["records"]))
        control.start(); self.wait_for(lambda: state.snapshot()["capture"] is not None); control.stop()
        self.assertEqual(len(self.files()), 2)  # recovery starts a new capture, never appends to the old one

    def test_unusable_capture_directory_blocks_monitoring(self):
        blocker = self.test_home / "not-a-directory"
        blocker.write_text("x")
        state, control = self.controller(capture_dir=blocker)
        control.start()
        self.wait_for(lambda: state.snapshot()["host"]["state"] == "ERROR")
        self.assertIn("E_CAPTURE", state.snapshot()["host"]["error"])
        self.assertEqual(state.snapshot()["receivers"], [])  # the TTY was never handed to the bridge
