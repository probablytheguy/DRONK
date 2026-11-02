"""Phase 2.1–2.3: migration 0002 and backfill, private imports, observation quality, historical queries."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import stat
import struct
import sys
import time
from types import SimpleNamespace
import urllib.error
import urllib.request
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from test_support import IsolatedConfigTestCase

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import capture_log as cl
import command_center as cc
import configuration as cfg
import dronk_db as ddb
import dronk_history as hist
from serial_sources import CONNECTED, DATA, DISCONNECTED
from test_store import FIELD, T0, StoreCase, observation, reception

S = 1000
FREE = SimpleNamespace(free=10 * 2**30)
FACTORY_ID = "c5-0123456789ab"
CHICAGO = hist.zone("America/Chicago")


def ms(text):
    return ddb.epoch_ms(text)


class Recorder:
    """A capture written the way the worker writes it: raw receiver bytes through envelope(), at chosen host times,
    and the bridge's decoded events. Uptime advances with host time unless given (buffered statuses)."""
    def __init__(self, directory, session="b" * 32):
        self.log = cl.CaptureLog(directory, "CAPTURE", ["front"], disk_usage=lambda _p: FREE)
        self.session, self.seq = session, 0

    def envelope(self, kind, at, payload=b""):
        self.log.envelope(struct.pack(">BHqI", kind, 0, at, len(payload)) + payload)

    def line(self, at, record):
        self.envelope(DATA, at, (json.dumps(record) + "\n").encode())

    def head(self, kind):
        self.seq += 1
        return dict(protocol="ouitak.receiver", version=1, type=kind, receiver_id=FACTORY_ID, session_id=self.session, seq=self.seq)

    def status(self, at, uptime_s=None, **counters):
        record = self.head("status")
        record.update(uptime_us=int(((at - T0) / 1000 + 3600 if uptime_s is None else uptime_s) * 1e6), channel=149,
                      counters=dict(dict(usb_output_drops=446, queue_drops=0, oversize_drops=0, malformed_frames=10, rf_frames=at), **counters))
        self.line(at, record)

    def reception(self, at, value="SYNTH-Q"):
        record = self.head("reception")
        record.update(transport="wifi_beacon", channel=149, basic_ids=[{"id_type": 1, "ua_type": 2, "value": value}], reason="no_valid_position")
        self.line(at, record)
        self.log.bridge(reception(at, session=self.session, seq=self.seq, value=value))

    def observation(self, at, lat, lon, value="SYNTH-Q"):
        record = self.head("observation")
        record.update(aircraft={"latitude": lat, "longitude": lon}, operator={"latitude": 12.3456, "longitude": -65.4321})
        self.line(at, record)
        event = observation(at, session=self.session, seq=self.seq, value=value, lat=lat, lon=lon)
        event["aircraft"]["operator"] = {"latitude": 12.3456, "longitude": -65.4321}
        self.log.bridge(event)

    def close(self, reason="requested"):
        self.log.close(reason)
        return self.log.path


class HistoryCase(StoreCase):
    def store(self, **kw):
        kw.setdefault("capture_dir", self.captures)
        return super().store(**kw)

    def quality_capture(self):
        """Connect; two statuses buffered on the receiver arrive first; fresh statuses every 5 s; receptions 40–80 s with
        a 16 s gap; +3 USB output drops (55–60 s) and +2 malformed frames (60–65 s); disconnect at 125 s."""
        r = Recorder(self.captures)
        r.envelope(CONNECTED, T0)
        r.status(T0 + 5, uptime_s=100, usb_output_drops=400)
        r.status(T0 + 6, uptime_s=105, usb_output_drops=446)  # +46 while nobody read: before the capture, not a loss in it
        receptions = [s for s in range(40, 81, 2) if not 50 < s < 66]
        for second in range(5, 121, 5):
            for rx in [s for s in receptions if second - 5 < s <= second]:
                r.reception(T0 + rx * S)
            r.status(T0 + second * S, usb_output_drops=449 if second >= 60 else 446, malformed_frames=12 if second >= 65 else 10)
        r.envelope(DISCONNECTED, T0 + 125 * S)
        return r.close(), len(receptions)

    def legacy_snapshot(self):
        events = [json.loads(line) for line in FIELD.read_text().splitlines()]
        logs = [{"time": e["reception"]["received_at"], "kind": "rid_reception", "event": e} for e in reversed(events)]
        path = self.test_home / "dronk-state-legacy.json"
        path.write_text(json.dumps({"mode": "DISCONNECTED", "logs": logs}))
        return path

    def sighting(self, store, identifier="SYNTH-Q"):
        return hist.sightings(store, "SERIAL_NUMBER", identifier, hist.zone("UTC"))["sightings"]


class QualityTests(HistoryCase):
    def test_coverage_losses_and_sighting_quality_from_a_capture(self):
        path, count = self.quality_capture()
        store = self.store()
        result = store.import_file(path)
        self.assertEqual(result["accepted"], count)
        cov, = store.db.execute("SELECT * FROM coverage").fetchall()
        self.assertEqual((cov["start_ms"], cov["end_ms"], cov["start_reason"], cov["end_reason"]), (T0 + 5, T0 + 120 * S, "connected", "disconnected"))
        losses = [tuple(r) for r in store.db.execute("SELECT kind, amount, certain, start_ms, end_ms FROM known_losses ORDER BY start_ms")]
        self.assertEqual(losses, [("usb_output_drops", 3, 1, T0 + 55 * S, T0 + 60 * S), ("malformed_frames", 2, 0, T0 + 60 * S, T0 + 65 * S)])
        s, = self.sighting(store)
        self.assertEqual(s["measured"]["receptions"], count)
        derived = s["derived"]
        self.assertEqual(derived["observed_duration_s"], 40.0)
        self.assertEqual(derived["packet_gaps"], dict(max_gap_s=16.0, median_gap_s=2.0, long_gaps=1, threshold_seconds=10))
        self.assertEqual(derived["gap_seconds"], 300)  # the 16 s gap is a quality warning, not a sighting split
        self.assertEqual({k: derived["coverage"][k] for k in ("state", "open_start", "open_end", "coverage_gaps", "disconnects", "losses", "possible_losses")},
                         dict(state="measured", open_start=False, open_end=False, coverage_gaps=0, disconnects=0, losses=1, possible_losses=1))
        self.assertEqual(store.check()["ok"], True)

    def test_status_silence_and_receiver_reboot_split_coverage(self):
        r = Recorder(self.captures)
        r.envelope(CONNECTED, T0)
        for second in range(0, 31, 5):
            r.status(T0 + second * S)
            if second == 25:
                r.reception(T0 + 26 * S)
        for second in range(60, 91, 5):  # 30 s of silence before this
            r.status(T0 + second * S)
            if second == 60:
                r.reception(T0 + 62 * S)
        r.session = "c" * 32  # receiver rebooted
        for second in range(95, 121, 5):
            r.status(T0 + second * S, uptime_s=second - 94)
        store = self.store()
        store.import_file(r.close())
        self.assertEqual([tuple(c) for c in store.db.execute("SELECT start_ms - ?, end_ms - ?, start_reason, end_reason FROM coverage ORDER BY start_ms", (T0, T0))],
                         [(0, 30 * S, "connected", "status_silence"), (60 * S, 90 * S, "resumed", "receiver_reboot"),
                          (95 * S, 120 * S, "receiver_reboot", "capture_end")])
        c = self.sighting(store)[0]["derived"]["coverage"]
        self.assertEqual((c["state"], c["open_start"], c["open_end"], c["coverage_gaps"]), ("measured", True, True, 1))
        return store

    def test_thresholds_are_configurable_recorded_and_persist(self):
        self.test_status_silence_and_receiver_reboot_split_coverage()
        store = self.store(silence_seconds=40, packet_gap_seconds=60)
        self.assertEqual(store.db.execute("SELECT count(*), min(silence_seconds) FROM coverage").fetchone()[:], (2, 40))
        s = self.sighting(store)[0]
        self.assertEqual((s["derived"]["packet_gaps"]["long_gaps"], s["derived"]["packet_gaps"]["threshold_seconds"],
                          s["derived"]["coverage"]["coverage_gaps"], s["derived"]["coverage"]["silence_seconds"]), (0, 60, 0, 40))
        self.assertEqual(s["measured"]["receptions"], 2)
        store.close()
        self.assertEqual(self.store().analysis["status_silence_seconds"], 40)  # omitted values keep the database's settings
        with self.assertRaises(ddb.StoreError):
            ddb.Store(self.db_path, packet_gap_seconds=300)  # must stay below the 300 s sighting gap
        with self.assertRaises(ddb.StoreError):
            ddb.Store(self.db_path, silence_seconds=3)

    def test_suspended_and_interrupted_captures_are_known_losses(self):
        r = Recorder(self.captures)
        r.envelope(CONNECTED, T0); r.status(T0); r.reception(T0 + S); r.status(T0 + 5 * S)
        r.log.suspend("low_disk_space")
        suspended = r.close()
        r = Recorder(self.captures)
        r.envelope(CONNECTED, T0 + 600 * S); r.status(T0 + 600 * S); r.reception(T0 + 601 * S)
        os.close(r.log.fd); r.log.fd = None  # killed: no capture_end
        with r.log.path.open("ab") as f:
            f.write(b'{"capture_id":"x","n":99,"type":"rx_')
        store = self.store()
        store.import_file(suspended); store.import_file(r.log.path)
        self.assertEqual(sorted(r[0] for r in store.db.execute("SELECT kind FROM known_losses")),
                         ["capture_suspended", "interrupted_capture", "truncated_record"])
        self.assertEqual(sorted(r[0] for r in store.db.execute("SELECT end_reason FROM coverage")), ["capture_suspended", "interrupted"])

    def test_legacy_snapshot_quality_stays_unknown(self):
        store = self.store()
        store.import_file(self.legacy_snapshot())
        self.assertEqual(store.status()["aircraft"], 1)
        s, = self.sighting(store, "FIELDTEST0000000001")
        self.assertEqual((s["measured"]["receptions"], s["measured"]["positioned_receptions"]), (66, 0))
        self.assertEqual(s["derived"]["coverage"]["state"], "unknown")
        self.assertEqual(s["derived"]["gps"], "none")
        self.assertEqual(store.db.execute("SELECT count(*) FROM coverage").fetchone()[0], 0)
        summary = hist.summary(store, "SERIAL_NUMBER", "FIELDTEST0000000001", CHICAGO)
        self.assertEqual(summary["measured"]["receptions_by_source_kind"], dict(capture=0, legacy_only=66))
        self.assertIn(hist.LEGACY_NOTE, summary["notes"])


class ImportStoreTests(HistoryCase):
    def test_imports_are_private_verified_copies_and_idempotent(self):
        original = self.legacy_snapshot()
        store = self.store()
        first, = store.import_files([original])
        copy, = store.imports.iterdir()
        self.assertEqual((first["accepted"], first["stored"]), (66, True))
        self.assertEqual(copy.read_bytes(), original.read_bytes())
        self.assertTrue(copy.name.startswith(ddb._sha256(original)[:16] + "-"))
        self.assertEqual((stat.S_IMODE(store.imports.stat().st_mode), stat.S_IMODE(copy.stat().st_mode)), (0o700, 0o400))
        again, = store.import_files([original])
        self.assertEqual((again["accepted"], again["already_imported"]), (0, 66))
        self.assertEqual(len(list(store.imports.iterdir())), 1)
        row = store.db.execute("SELECT file_name, stored_name, file_sha256 FROM sources").fetchone()
        self.assertEqual((row[0], row[1], row[2]), (original.name, copy.name, ddb._sha256(original)))
        path, _ = self.quality_capture()
        store.import_files([path])  # already in the capture directory: imported in place, not copied
        self.assertEqual(len(list(store.imports.iterdir())), 1)

    def test_batch_limits_and_free_space_are_checked_before_copying(self):
        original = self.legacy_snapshot()
        store = self.store()
        with self.assertRaises(ddb.StoreError):
            store.import_files([original] * 51)
        with patch.object(ddb, "IMPORT_MAX_BYTES", 100):
            result, = store.import_files([original])
        self.assertIn("2 GiB", result["error"])
        store.disk_usage = lambda _p: SimpleNamespace(free=ddb.IMPORT_MIN_FREE_BYTES + 10)
        with self.assertRaises(ddb.StoreError):
            store.import_files([original, self.test_home])
        self.assertEqual(list(store.imports.iterdir()), [])
        self.assertEqual(store.status()["sources"], 0)

    def test_interrupted_copy_leaves_nothing_and_old_partials_are_removed(self):
        original = self.legacy_snapshot()
        store = self.store()
        cl.private_directory(store.imports)
        stale = store.imports / ".partial-abandoned"
        stale.write_bytes(b"half")
        os.utime(stale, (time.time() - 2 * ddb.PARTIAL_MAX_AGE,) * 2)
        with patch.object(ddb.os, "fsync", side_effect=OSError(28, "No space left on device")):
            result, = store.import_files([original])
        self.assertEqual(result["error"], "No space left on device")
        self.assertEqual(list(store.imports.iterdir()), [])
        self.assertEqual(store.status()["sources"], 0)

    def test_capture_still_being_written_is_not_copied(self):
        r = Recorder(self.test_home / "elsewhere")
        r.reception(T0)
        store = self.store()
        result, = store.import_files([r.log.path])
        self.assertIn("still being written", result["error"])
        r.close()


class MigrationBackfillTests(HistoryCase):
    ADDED = dict(sightings=("max_gap_ms", "median_gap_ms", "long_gaps", "packet_gap_seconds", "coverage", "open_start", "open_end", "coverage_gaps",
                            "disconnects", "losses", "possible_losses", "edge_seconds", "silence_seconds", "quality_derivation"),
                 reception_sources=("byte_offset",), sources=("stored_name", "file_bytes", "index_version"))

    def as_phase1(self, path):
        """Turn a database into exactly the Phase 1 (v1) schema, keeping its Phase 1 data."""
        db = sqlite3.connect(path)
        for table in ("marker_states", "capture_events", "receiver_lines", "coverage", "known_losses", "settings"):
            db.execute(f"DROP TABLE {table}")
        for table, columns in self.ADDED.items():
            for column in columns:
                db.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        db.execute("DELETE FROM schema_migrations WHERE version >= 2")
        db.execute("PRAGMA user_version = 1")
        db.commit(); db.close()

    def test_phase1_database_upgrades_with_verified_backup_and_backfills(self):
        path, count = self.quality_capture()
        legacy = self.legacy_snapshot()
        store = self.store()
        store.import_file(path); store.import_file(legacy)  # Phase 1 style: legacy imported in place, no stored copy
        before = store.status()
        store.close()
        self.as_phase1(self.db_path)

        store = self.store()
        self.assertEqual(store.schema_version(), 3)
        backup, = self.dir.glob("dronk.sqlite3.bak-v1-*")
        old = sqlite3.connect(backup)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        self.assertEqual((old.execute("PRAGMA user_version").fetchone()[0], old.execute("PRAGMA integrity_check").fetchone()[0],
                          old.execute("SELECT count(*) FROM receptions").fetchone()[0]), (1, "ok", count + 66))
        old.close()
        for key in ("aircraft", "receptions", "provenance", "sightings", "sources"):
            self.assertEqual(store.status()[key], before[key], key)
        check = store.check()
        self.assertFalse(check["ok"])
        self.assertEqual(len(check["not_backfilled"]), 2)

        result, = ddb.sync_captures(store, self.captures)  # backfill: offsets, events, lines; receptions unchanged
        self.assertEqual((result["accepted"], result["already_imported"]), (0, count))
        legacy_result, = store.import_files([legacy])
        self.assertEqual((legacy_result["accepted"], legacy_result["already_imported"], legacy_result["stored"]), (0, 66, True))
        check = store.check()
        self.assertTrue(check["ok"], check)
        self.assertEqual(check["legacy_without_stored_copy"], [])
        a = {r["identifier"]: r for r in store.search("")}
        self.assertEqual((a["FIELDTEST0000000001"]["reception_count"], a["FIELDTEST0000000001"]["sightings"]), (66, 1))
        upgraded = store.fingerprint()
        store.close()

        with patch("sys.stdout"):
            self.assertEqual(ddb.main(["--db", str(self.db_path), "--captures", str(self.captures), "rebuild"]), 0)
        rebuilt = self.store()
        self.assertEqual(rebuilt.fingerprint(), upgraded)  # incremental upgrade == rebuild from captures/ and imports/
        self.assertTrue(rebuilt.check()["ok"])

    def test_backup_that_does_not_verify_stops_the_migration(self):
        self.store().close()
        self.as_phase1(self.db_path)
        with patch.object(ddb.Store, "schema_version", return_value=99):  # the copy reads back as another version
            with self.assertRaises(ddb.StoreError):
                ddb.Store(self.db_path)
        db = sqlite3.connect(self.db_path)
        self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
        db.close()

    def test_live_index_records_byte_offsets(self):
        log = cl.CaptureLog(self.captures, "CAPTURE", ["front"], disk_usage=lambda _p: FREE)
        indexer = ddb.LiveIndexer(self.db_path, log, interval=0.05)
        indexer.start()
        for i in range(1, 4):
            event = reception(T0 + i * S, seq=i)
            position = log.bridge(event)
            indexer.offer(*position, event)
        indexer.close(); log.close("requested")
        store = self.store()
        data = log.path.read_bytes()
        for n, offset in store.db.execute("SELECT record_n, byte_offset FROM reception_sources"):
            self.assertEqual(json.loads(data[offset:data.index(b"\n", offset)])["n"], n)


class HistoryQueryTests(HistoryCase):
    def test_local_days_daylight_saving_and_iso_weeks(self):
        times = ["2026-03-08T05:30:00Z", "2026-03-08T06:30:00Z",  # Chicago: Mar 7 23:30 CST, Mar 8 00:30 CST
                 "2026-07-01T04:59:00Z", "2026-07-01T05:01:00Z",  # Jun 30 23:59 CDT, Jul 1 00:01 CDT
                 "2026-12-31T12:00:00Z", "2027-01-01T12:00:00Z"]  # both ISO week 2026-W53
        fallback = [ms("2026-11-01T06:30:00Z") + i * 240 * S for i in range(16)]  # 01:30 CDT .. 01:30 CST: one sighting, 1 h
        events = [reception(ms(t), seq=i) for i, t in enumerate(times, 1)] + [reception(t, seq=100 + i) for i, t in enumerate(fallback)]
        store = self.store()
        store.import_file(self.capture(events))
        days = {p["period"]: p for p in hist.appearances(store, "SERIAL_NUMBER", "SYNTH-ONE", CHICAGO, "day")["periods"]}
        self.assertEqual(sorted(days), ["2026-03-07", "2026-03-08", "2026-06-30", "2026-07-01", "2026-11-01", "2026-12-31", "2027-01-01"])
        self.assertEqual((days["2026-03-08"]["hours"], days["2026-11-01"]["hours"], days["2026-07-01"]["hours"]), (23, 25, 24))
        self.assertEqual((days["2026-03-08"]["start"], days["2026-07-01"]["start"]), ("2026-03-08T00:00:00-06:00", "2026-07-01T00:00:00-05:00"))
        utc_days = [p["period"] for p in hist.appearances(store, "SERIAL_NUMBER", "SYNTH-ONE", hist.zone("UTC"), "day")["periods"]]
        self.assertEqual(utc_days, ["2026-03-08", "2026-07-01", "2026-11-01", "2026-12-31", "2027-01-01"])
        weeks = {p["period"]: p["receptions"] for p in hist.appearances(store, "SERIAL_NUMBER", "SYNTH-ONE", CHICAGO, "week")["periods"]}
        self.assertEqual(weeks["2026-W53"], 2)
        months = {p["period"]: p["receptions"] for p in hist.appearances(store, "SERIAL_NUMBER", "SYNTH-ONE", CHICAGO, "month")["periods"]}
        self.assertEqual((months["2026-03"], months["2026-06"], months["2026-07"]), (2, 1, 1))
        s = next(s for s in hist.sightings(store, "SERIAL_NUMBER", "SYNTH-ONE", CHICAGO)["sightings"] if s["measured"]["receptions"] == 16)
        self.assertEqual((s["measured"]["first_reception"]["local"], s["measured"]["last_reception"]["local"]),
                         ("2026-11-01T01:30:00.000-05:00", "2026-11-01T01:30:00.000-06:00"))
        self.assertEqual(s["derived"]["observed_duration_s"], 3600.0)
        summary = hist.summary(store, "SERIAL_NUMBER", "SYNTH-ONE", CHICAGO)
        self.assertEqual((summary["derived"]["distinct_days"], summary["measured"]["first_reception"]["local"]), (7, "2026-03-07T23:30:00.000-06:00"))
        with self.assertRaises(ddb.StoreError):
            hist.zone("Mars/Olympus_Mons")

    def positioned_capture(self):
        r = Recorder(self.captures)
        r.envelope(CONNECTED, T0)
        r.status(T0)
        r.observation(T0 + 1 * S, 38.6001, -90.2001)
        r.reception(T0 + 2 * S)
        r.observation(T0 + 3 * S, 38.6003, -90.2003)
        r.status(T0 + 5 * S)
        return r.close()

    def test_tracks_are_aircraft_positions_only_and_provenance_verifies_files(self):
        path = self.positioned_capture()
        store = self.store()
        store.import_file(path)
        track = hist.track(store, "SERIAL_NUMBER", "SYNTH-Q", CHICAGO)
        self.assertEqual([(p["latitude"], p["longitude"]) for p in track["points"]], [(38.6001, -90.2001), (38.6003, -90.2003)])
        self.assertEqual((track["receptions_without_position"], track["layers"]), (1, dict(aircraft=True, operator=False, takeoff=False)))
        self.assertNotIn("12.3456", json.dumps(track))
        proof = hist.provenance(store, track["points"][0]["reception_id"], CHICAGO)
        o, = proof["occurrences"]
        self.assertEqual((o["status"], o["raw_line"]["status"], o["raw_line"]["line"]["type"]), ("verified", "verified", "observation"))
        self.assertEqual(o["stored_record"]["event"]["aircraft"]["operator"], "[withheld]")
        self.assertEqual(o["raw_line"]["line"]["operator"], "[withheld]")
        text = json.dumps(proof)
        self.assertNotIn("12.3456", text)
        self.assertNotIn(FACTORY_ID, text)
        self.assertIsNotNone(o["byte_offset"])
        rx = hist.receptions(store, "SERIAL_NUMBER", "SYNTH-Q", CHICAGO, limit=2)
        self.assertEqual((len(rx["receptions"]), rx["receptions"][0]["legacy"]), (2, False))
        older = hist.receptions(store, "SERIAL_NUMBER", "SYNTH-Q", CHICAGO, before_ms=rx["next_before"], limit=2)
        self.assertEqual(len(older["receptions"]), 1)
        path.unlink()
        self.assertEqual(hist.provenance(store, track["points"][0]["reception_id"], CHICAGO)["occurrences"][0]["status"], "file_missing")

    def test_legacy_provenance_needs_the_stored_copy(self):
        store = self.store()
        legacy = self.legacy_snapshot()
        store.import_file(legacy)
        first = store.db.execute("SELECT min(id) FROM receptions").fetchone()[0]
        self.assertEqual(hist.provenance(store, first, CHICAGO)["occurrences"][0]["status"], "not_stored")
        store.import_files([legacy])
        o, = hist.provenance(store, first, CHICAGO)["occurrences"]
        self.assertEqual((o["status"], o["source"]["kind"], o["note"]), ("verified", "dashboard_state", hist.LEGACY_NOTE))

    def test_coverage_query_separates_measured_events_from_derived_intervals(self):
        path, _ = self.quality_capture()
        store = self.store()
        store.import_file(path)
        result = hist.coverage(store, T0 - 60 * S, T0 + 600 * S, CHICAGO)
        self.assertEqual([e["event"] for e in result["measured"]["events"] if e["type"] == "usb"], ["connected", "disconnected"])
        self.assertEqual(len(result["derived"]["coverage"]), 1)
        self.assertEqual([l["kind"] for l in result["derived"]["known_losses"]], ["usb_output_drops", "malformed_frames"])
        self.assertEqual(result["range"]["start"]["local"][-6:], "-06:00")  # Nov 2: CST
        with self.assertRaises(ddb.StoreError):
            hist.coverage(store, T0, T0 - 1, CHICAGO)


class HistoryApiTests(HistoryCase):
    def test_history_routes(self):
        path, _ = self.quality_capture()
        store = self.store()
        store.import_file(path)
        server = cc.make_server(cc.State(), 0)
        server.store = store
        self.addCleanup(server.server_close)
        import threading
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        def get(path):
            try:
                with urllib.request.urlopen(base + path, timeout=5) as response:
                    return response.status, json.loads(response.read())
            except urllib.error.HTTPError as error:
                with error:
                    return error.code, error.read().decode()
        code, body = get("/api/aircraft/summary?type=SERIAL_NUMBER&id=SYNTH-Q")
        self.assertEqual((code, body["time_zone"], body["derived"]["sightings"]), (200, "America/Chicago", 1))
        self.assertEqual(set(body), {"aircraft", "time_zone", "measured", "derived", "notes"})
        code, body = get("/api/aircraft/sightings?type=SERIAL_NUMBER&id=SYNTH-Q&tz=UTC")
        self.assertEqual((code, body["sightings"][0]["measured"]["first_reception"]["local"][-6:]), (200, "+00:00"))
        code, body = get("/api/aircraft/appearances?type=SERIAL_NUMBER&id=SYNTH-Q&period=week")
        self.assertEqual((code, body["periods"][0]["period"]), (200, "2026-W45"))
        code, body = get("/api/coverage?from=2026-11-02T13:00:00Z&to=2026-11-02T14:00:00Z")
        self.assertEqual((code, len(body["derived"]["coverage"])), (200, 1))
        rid = store.db.execute("SELECT min(id) FROM receptions").fetchone()[0]
        code, body = get(f"/api/provenance?reception={rid}")
        self.assertEqual((code, body["occurrences"][0]["status"]), (200, "verified"))
        self.assertEqual(get("/api/aircraft/summary?type=SERIAL_NUMBER&id=NOPE")[0], 404)
        self.assertEqual(get("/api/aircraft/summary?type=SERIAL_NUMBER&id=SYNTH-Q&tz=Nowhere/Else")[0], 400)
        self.assertEqual(get("/api/aircraft/appearances?type=SERIAL_NUMBER&id=SYNTH-Q&period=year")[0], 400)
        self.assertEqual(get("/api/provenance?reception=x")[0], 400)


class AnalysisConfigTests(IsolatedConfigTestCase):
    def args(self, **kw):
        return argparse.Namespace(**dict(dict(mode=None, demo=False, capture_dir=None, no_capture=False), **kw))

    def configure(self, text):
        path = self.test_home / ".config/dronk/config.toml"
        path.write_text('mode="CAPTURE"\n' + text)

    def test_time_zone_defaults_to_chicago_and_thresholds_come_from_toml(self):
        result = cfg.resolve(self.args(), {"front": "/dev/DO_NOT_OPEN"}, {})
        self.assertEqual(result["analysis"]["time_zone"], "America/Chicago")
        self.assertIsNone(result["analysis"]["packet_gap_seconds"])
        self.configure('[analysis]\ntime_zone="Europe/Berlin"\npacket_gap_seconds=20\n')
        result = cfg.resolve(self.args(), {"front": "/dev/DO_NOT_OPEN"}, {})
        self.assertEqual((result["analysis"]["time_zone"], result["analysis"]["packet_gap_seconds"]), ("Europe/Berlin", 20))
        self.assertEqual(cfg.resolve(self.args(time_zone="UTC"), {"front": "/dev/DO_NOT_OPEN"}, {})["analysis"]["time_zone"], "UTC")

    def test_invalid_analysis_settings_are_rejected(self):
        for text in ('[analysis]\ntime_zone="Not/AZone"\n', '[analysis]\nbogus=1\n', '[analysis]\nedge_seconds="30"\n'):
            self.configure(text)
            with self.assertRaises(ValueError, msg=text):
                cfg.resolve(self.args(), {"front": "/dev/DO_NOT_OPEN"}, {})


def marker(state, reason, role="aircraft", last_seen=T0 + 3 * S, reported=T0 + 63 * S, lat=38.6003, lon=-90.2003, value="SYNTH-Q"):
    uid = "ouitak-rid-test" + ("" if role == "aircraft" else "-" + role)
    return {"kind": "marker_state", "uid": uid, "aircraft_uid": "ouitak-rid-test", "id": value, "identifier_type": "SERIAL_NUMBER",
            "synthetic": False, "role": role, "state": state, "reason": reason, "last_seen": ddb.iso_ms(last_seen),
            "reported_at": ddb.iso_ms(reported) if state == "LAST_KNOWN" else None,
            "stale_at": ddb.iso_ms(reported + 600 * S) if state == "LAST_KNOWN" else None, "stream": "front",
            "position": {"latitude": lat, "longitude": lon, "altitude_hae_m": 120.0}}


class LastKnownTests(HistoryCase):
    def test_marker_transitions_are_captured_indexed_and_kept(self):
        r = Recorder(self.captures)
        r.envelope(CONNECTED, T0); r.status(T0)
        r.observation(T0 + 3 * S, 38.6003, -90.2003)
        r.log.bridge(marker("LAST_KNOWN", "no_receptions"))
        r.log.bridge(marker("LAST_KNOWN", "no_receptions", role="operator", lat=12.3456, lon=-65.4321))
        r.log.bridge(marker("LIVE", "receptions_resumed", last_seen=T0 + 200 * S, reported=None))
        path = r.close()
        store = self.store()
        result = store.import_file(path)
        self.assertEqual(result["marker_state"], 3)
        self.assertEqual(store.import_file(path)["marker_state"], 0)  # idempotent
        rows = hist.markers(store, "SERIAL_NUMBER", "SYNTH-Q", CHICAGO)["markers"]
        self.assertEqual(sorted((m["role"], m["state"]) for m in rows), [("aircraft", "LAST_KNOWN"), ("aircraft", "LIVE"), ("operator", "LAST_KNOWN")])
        operator = next(m for m in rows if m["role"] == "operator")
        self.assertEqual((operator["position"]["latitude"], operator["last_seen"]["utc"]), (12.3456, ddb.iso_ms(T0 + 3 * S)))
        self.assertTrue(store.check()["ok"])
        self.assertEqual(len(store.fingerprint()["marker_states"]), 3)

    def test_live_index_keeps_marker_transitions(self):
        log = cl.CaptureLog(self.captures, "CAPTURE", ["front"], disk_usage=lambda _p: FREE)
        indexer = ddb.LiveIndexer(self.db_path, log, interval=0.05)
        indexer.start()
        for event in (reception(T0 + S, value="SYNTH-Q"), marker("LAST_KNOWN", "receiver_interrupted")):
            indexer.offer(*log.bridge(event), event)
        indexer.close(); log.close("requested")
        store = self.store()
        self.assertEqual(store.db.execute("SELECT state, reason FROM marker_states").fetchall()[0][:], ("LAST_KNOWN", "receiver_interrupted"))

    def test_console_shows_last_known_until_a_newer_reception(self):
        state = cc.State()
        now = time.time()
        state.apply(observation(int(now * 1000) - 70_000, value="SYNTH-Q"))
        event = marker("LAST_KNOWN", "receiver_interrupted", last_seen=int(now * 1000) - 70_000, reported=int(now * 1000) - 10_000)
        state.apply(event)
        d, = state.snapshot()["detections"]
        self.assertEqual((d["state"], d["marker"]["reason"]), ("LAST_KNOWN", "receiver_interrupted"))
        state.apply(observation(int(now * 1000), seq=2, value="SYNTH-Q"))
        self.assertEqual(state.snapshot()["detections"][0]["state"], "TRACKING")
