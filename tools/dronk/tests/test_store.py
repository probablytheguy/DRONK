"""SQLite store: migrations, imports, dedup, sightings, live indexing, recovery (Phase 1; Phase 2 in test_history.py)."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from test_support import IsolatedConfigTestCase

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import capture_log as cl
import command_center as cc
import dronk_db as ddb
from test_detection_panel import PanelStateCase, iso

FIELD = Path(__file__).with_name("fixtures") / "field-2026-11-02-avata-receptions.jsonl"
DAY = 86_400_000
T0 = 1_793_626_757_759  # 2026-11-02T13:39:17.759Z
LATEST = max(int(f.name[:4]) for f in ddb.MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql"))


def reception(ms, session="a" * 32, seq=1, value="SYNTH-ONE", reason="no_valid_position", receiver="front", synthetic=False):
    r = {"stream": receiver, "receiver_id": receiver, "transmitter": "02:00:00:00:00:01", "transport": "wifi_beacon", "channel": 149,
         "frequency_mhz": 5745, "rssi_dbm": -50, "message_counter": seq % 256, "odid_protocol_version": 2, "decoded_messages": 3,
         "messages": ["basic_id", "location", "system"], "basic_ids": [{"id_type": 1, "ua_type": 2, "value": value}],
         "aircraft_location": "unavailable", "reason": reason, "observed_at": ddb.iso_ms(ms), "received_at": ddb.iso_ms(ms),
         "synthetic": synthetic}
    if session is not None:
        r.update(session_id=session, seq=str(seq))
    return {"kind": "rid_reception", "reception": r}


def observation(ms, session="a" * 32, seq=1, value="SYNTH-ONE", lat=38.6, lon=-90.2):
    return {"kind": "observation", "expires_at": ddb.iso_ms(ms + 60000), "aircraft": {
        "uid": "u-" + value, "id": value, "identifier_type": "SERIAL_NUMBER", "source": "DRONK_C5_USB", "synthetic": False,
        "observed_at": ddb.iso_ms(ms), "received_at": ddb.iso_ms(ms), "position": {"latitude": lat, "longitude": lon, "altitude_hae_m": 120.0},
        "rf": {"stream": "front", "receiver_id": "front", "session_id": session, "seq": str(seq), "transport": "wifi_beacon",
               "channel": 149, "frequency_mhz": 5745, "rssi_dbm": -48, "transmitter": "02:00:00:00:00:01", "message_counter": 3}}}


class StoreCase(IsolatedConfigTestCase):
    def setUp(self):
        super().setUp()
        self.dir = self.test_home / "state"
        self.captures = self.dir / "captures"
        self.db_path = self.dir / "dronk.sqlite3"

    def store(self, **kw):
        store = ddb.Store(kw.pop("path", self.db_path), **kw)
        self.addCleanup(store.close)
        return store

    def capture(self, events, close="requested"):
        """A real capture file containing these bridge events (plus telemetry), as the worker writes it."""
        log = cl.CaptureLog(self.captures, "CAPTURE", ["front"], disk_usage=lambda _p: SimpleNamespace(free=10 * 2**30))
        log.bridge({"kind": "ready", "mode": "CAPTURE", "stale_seconds": 60})
        for event in events:
            log.bridge(event)
        if close:
            log.close(close)
        else:
            os.close(log.fd); log.fd = None
        return log.path

    def count(self, store, table, where="1"):
        return store.db.execute(f"SELECT count(*) FROM {table} WHERE {where}").fetchone()[0]


class MigrationTests(StoreCase):
    def test_fresh_database_is_private_and_versioned(self):
        store = self.store()
        self.assertEqual(store.schema_version(), LATEST)
        self.assertEqual([tuple(r) for r in store.db.execute("SELECT version, description FROM schema_migrations")][:3],
                         [(1, "initial"), (2, "observation_quality"), (3, "marker_states")])
        self.assertEqual(stat.S_IMODE(self.db_path.stat().st_mode), 0o600)
        self.assertEqual(store.db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        store.close()
        self.store()  # reopen: nothing pending, no backup
        self.assertEqual(list(self.dir.glob("*.bak-*")), [])

    def test_new_state_directories_are_private(self):
        nested = self.test_home / "fresh-state" / "dronk"
        self.store(path=nested / "dronk.sqlite3")
        cl.CaptureLog(nested / "captures", "CAPTURE", ["front"], disk_usage=lambda _p: SimpleNamespace(free=10 * 2**30)).close("requested")
        for directory in (self.test_home / "fresh-state", nested, nested / "captures"):
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700, directory)

    def test_newer_database_is_refused(self):
        self.store().db.execute("PRAGMA user_version = 99")
        with self.assertRaises(ddb.StoreError):
            ddb.Store(self.db_path)

    def migrations(self, extra_sql):
        directory = self.test_home / "migrations"
        directory.mkdir()
        for migration in ddb.MIGRATIONS.glob("*.sql"):
            shutil.copy(migration, directory)
        (directory / f"{LATEST + 1:04d}_next.sql").write_text(extra_sql)
        return directory

    def test_upgrade_backs_up_first_and_keeps_data(self):
        store = self.store()
        store.import_file(self.capture([reception(T0)]))
        store.close()
        upgraded = self.store(migrations=self.migrations("CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT);"))
        self.assertEqual(upgraded.schema_version(), LATEST + 1)
        backup, = self.dir.glob(f"dronk.sqlite3.bak-v{LATEST}-*")
        old = sqlite3.connect(backup)
        self.assertEqual((old.execute("PRAGMA user_version").fetchone()[0], old.execute("SELECT count(*) FROM receptions").fetchone()[0]), (LATEST, 1))
        old.close()
        self.assertEqual(self.count(upgraded, "receptions"), 1)

    def test_failed_migration_rolls_back(self):
        self.store().close()
        with self.assertRaises(sqlite3.Error):
            ddb.Store(self.db_path, migrations=self.migrations("CREATE TABLE ok (id INTEGER); THIS IS NOT SQL;"))
        store = self.store()
        self.assertEqual(store.schema_version(), LATEST)
        self.assertIsNone(store.db.execute("SELECT name FROM sqlite_master WHERE name = 'ok'").fetchone())


class ImportTests(StoreCase):
    def legacy_snapshot(self):
        """The sanitized 2026-11-02 events in the dashboard /api/state layout the real file uses (newest first)."""
        events = [json.loads(line) for line in FIELD.read_text().splitlines()]
        logs = [{"time": e["reception"]["received_at"], "kind": "rid_reception", "event": e} for e in reversed(events)]
        logs.append({"time": "2026-11-02T13:39:00Z", "kind": "receiver", "event": {"kind": "receiver"}})
        path = self.test_home / "dronk-state-legacy.json"
        path.write_text(json.dumps({"mode": "DISCONNECTED", "logs": logs}))
        return path

    def test_legacy_november_2_imports_as_one_positionless_aircraft(self):
        store = self.store()
        result = store.import_file(self.legacy_snapshot())
        self.assertEqual((result["kind"], result["accepted"], result["rejected"], result["other_records"]), ("dashboard_state", 66, 0, 1))
        a, = store.search("FIELDTEST")
        self.assertEqual((a["reception_count"], a["positioned_count"], a["sightings"]), (66, 0, 1))
        self.assertEqual((a["first_seen"], a["last_seen"]), ("2026-11-02T13:39:17.759Z", "2026-11-02T13:40:38.079Z"))
        self.assertEqual(self.count(store, "receptions", "latitude IS NOT NULL OR longitude IS NOT NULL OR position_valid = 1"), 0)
        self.assertEqual(self.count(store, "receptions", "dedup_key LIKE 'legacy:%' AND receiver_session_id IS NULL"), 66)
        again = store.import_file(self.legacy_snapshot())
        self.assertEqual((again["accepted"], again["already_imported"]), (0, 66))
        self.assertEqual(store.search("FIELDTEST")[0]["reception_count"], 66)

    def test_three_captures_reconcile_overlaps_without_inflating_counts(self):
        day1 = [reception(T0 + i * 1000, seq=i) for i in range(1, 11)]
        day2 = [reception(T0 + DAY + i * 1000, session="b" * 32, seq=i) for i in range(1, 6)] + \
               [observation(T0 + DAY + 9000, session="b" * 32, seq=9)]
        a, b = self.capture(day1), self.capture(day2)
        overlap = self.capture(day1[5:] + [reception(T0 + 20000, seq=20)])  # another capture holding 5 of day 1's records
        copy = self.test_home / "copy-of-a.jsonl"
        shutil.copy(a, copy)  # same file under another name: same capture_id
        store = self.store()
        results = [store.import_file(p) for p in (b, overlap, a, copy)]
        self.assertEqual([(r["accepted"], r["duplicate"], r["already_imported"]) for r in results],
                         [(6, 0, 0), (6, 0, 0), (5, 5, 0), (0, 0, 10)])
        aircraft, = store.search("SYNTH-ONE")
        self.assertEqual((aircraft["reception_count"], aircraft["positioned_count"]), (17, 1))
        self.assertEqual(self.count(store, "receptions"), 17)
        self.assertEqual(self.count(store, "reception_sources"), 22)  # every place each reception was found
        detail = store.aircraft_detail("SERIAL_NUMBER", "SYNTH-ONE")
        self.assertEqual(detail["aircraft"]["first_seen"], ddb.iso_ms(T0 + 1000))
        self.assertEqual(detail["aircraft"]["last_seen"], ddb.iso_ms(T0 + DAY + 9000))
        self.assertEqual([s["reception_count"] for s in detail["sightings"]], [6, 11])  # packets vs. windows, spanning two days
        self.assertTrue(all(s["gap_seconds"] == 300 and s["derivation"] == "gap-v1" for s in detail["sightings"]))
        shared = next(r for r in detail["timeline"] if r["received_ms"] == T0 + 7000)
        self.assertEqual(len(shared["provenance"]), 2)
        tracked = next(r for r in detail["timeline"] if r["record_kind"] == "observation")
        self.assertEqual((tracked["position_valid"], tracked["position"]["latitude"]), (True, 38.6))
        rerun = [store.import_file(p) for p in (a, b, overlap, copy)]
        self.assertTrue(all(r["accepted"] == 0 for r in rerun))
        self.assertEqual(store.search("SYNTH-ONE")[0]["reception_count"], 17)

    def test_sequence_restarts_per_boot_and_conflicting_keys_never_merge(self):
        store = self.store()
        result = store.import_file(self.capture([
            reception(T0, session="a" * 32, seq=4), reception(T0 + 1000, session="b" * 32, seq=4),  # reboot: seq restarts
            reception(T0 + 2000, session="a" * 32, seq=4)]))  # same key, different content: reused session ID
        self.assertEqual((result["accepted"], result["conflict"]), (3, 1))
        self.assertEqual(store.search("SYNTH-ONE")[0]["reception_count"], 3)
        self.assertEqual(self.count(store, "receiver_sessions"), 2)
        self.assertEqual(self.count(store, "receptions", "dedup_key LIKE 'rx:%#%'"), 1)

    def test_sighting_gap_is_configurable_and_recorded(self):
        path = self.capture([reception(T0 + s * 1000, seq=i) for i, s in enumerate((0, 100, 500, 560, 2000), 1)])
        store = self.store()
        store.import_file(path)
        self.assertEqual([s["reception_count"] for s in store.aircraft_detail("SERIAL_NUMBER", "SYNTH-ONE")["sightings"]], [1, 2, 2])
        store.close()
        wider = self.store(gap_seconds=600)  # reopening with another gap rebuilds the derived table
        self.assertEqual([(s["reception_count"], s["gap_seconds"]) for s in wider.aircraft_detail("SERIAL_NUMBER", "SYNTH-ONE")["sightings"]],
                         [(1, 600), (4, 600)])

    def test_unusable_records_are_rejected_with_reasons(self):
        bad_position = observation(T0, seq=2, lat=91.0)
        no_id = reception(T0, seq=3, reason="no_basic_id"); no_id["reception"]["basic_ids"] = []
        events = [reception(T0, seq=1, synthetic=True), bad_position, {"kind": "rid_reception", "reception": {"x": 1}},
                  dict(reception(T0, seq=4), reception=dict(reception(T0, seq=4)["reception"], received_at="yesterday")), no_id]
        store = self.store()
        result = store.import_file(self.capture(events))
        self.assertEqual((result["accepted"], result["rejected"]), (1, 4))
        self.assertEqual(result["rejected_reasons"], {"synthetic": 1, "position": 1, "messages": 1, "timestamp": 1})
        self.assertEqual(self.count(store, "receptions", "aircraft_id IS NULL"), 1)  # no Basic ID: kept, never an aircraft
        self.assertEqual(self.count(store, "aircraft"), 0)

    def test_synthetic_capture_needs_its_own_database(self):
        log = cl.CaptureLog(self.captures, "DEMO", ["demo-east"], synthetic=True, disk_usage=lambda _p: SimpleNamespace(free=10 * 2**30))
        log.close("requested")
        with self.assertRaises(ddb.StoreError):
            self.store().import_file(log.path)

    def test_interrupted_import_resumes_without_double_counting(self):
        path = self.capture([reception(T0 + i * 1000, seq=i) for i in range(1, 1201)])
        store = self.store()
        with self.assertRaises(KeyboardInterrupt):
            store.import_file(path, interrupt_after=700)
        self.assertEqual(self.count(store, "receptions"), 700)  # committed batches survive
        result = store.import_file(path)
        self.assertEqual((result["accepted"], result["already_imported"]), (500, 700))
        a, = store.search("SYNTH-ONE")
        self.assertEqual((a["reception_count"], a["sightings"]), (1200, 1))

    def test_interrupted_capture_file_imports_complete_records(self):
        path = self.capture([reception(T0, seq=1), reception(T0 + 1000, seq=2)], close=None)
        with path.open("ab") as f:
            f.write(b'{"capture_id":"x","n":9,"type":"bri')
        result = self.store().import_file(path)
        self.assertEqual((result["accepted"], result["complete"], result["truncated_tail"]), (2, False, True))

    def test_search_escapes_wildcards(self):
        store = self.store()
        store.import_file(self.capture([reception(T0, seq=1, value="AB%C"), reception(T0, seq=2, value="ABXC")]))
        self.assertEqual([a["identifier"] for a in store.search("B%")], ["AB%C"])
        self.assertIsNone(store.aircraft_detail("SERIAL_NUMBER", "NOPE"))

    def test_rebuild_cli_recreates_from_captures_and_keeps_old_database(self):
        self.capture([reception(T0 + i * 1000, seq=i) for i in range(1, 4)])
        store = self.store(); store.import_file(next(self.captures.glob("*.jsonl"))); store.close()
        with patch("sys.stdout"):
            code = ddb.main(["--db", str(self.db_path), "rebuild", "--captures", str(self.captures)])
        self.assertEqual(code, 0)
        self.assertEqual(len(list(self.dir.glob("dronk.sqlite3.bak-rebuild-*"))), 1)
        self.assertEqual(self.store().search("SYNTH")[0]["reception_count"], 3)


class LiveIndexTests(StoreCase):
    def live(self, store_factory=None, **kw):
        log = cl.CaptureLog(self.captures, "CAPTURE", ["front"], disk_usage=lambda _p: SimpleNamespace(free=10 * 2**30))
        indexer = ddb.LiveIndexer(self.db_path, log, interval=0.05, store_factory=store_factory, **kw)
        indexer.start()
        return log, indexer

    def feed(self, log, indexer, events):
        for event in events:
            position = log.bridge(event)
            if position:
                indexer.offer(*position, event)

    def test_live_index_matches_the_capture_and_later_import_adds_nothing(self):
        log, indexer = self.live()
        self.feed(log, indexer, [{"kind": "tak", "state": "DISABLED"}] + [reception(T0 + i, seq=i) for i in range(1, 51)])
        indexer.close(); log.close("requested")
        self.assertEqual((indexer.state, indexer.indexed, indexer.dropped), ("CLOSED", 50, 0))
        store = self.store()
        self.assertEqual(store.search("SYNTH-ONE")[0]["reception_count"], 50)
        source = store.db.execute("SELECT needs_reimport, file_sha256 FROM sources").fetchone()
        self.assertEqual(tuple(source), (0, None))  # not yet verified against the closed file
        result, = ddb.sync_captures(store, self.captures)  # just closed (capture_end): not skipped as in use
        self.assertEqual((result["accepted"], result["already_imported"]), (0, 50))
        self.assertEqual(ddb.sync_captures(store, self.captures), [])  # verified: not scanned again

    def test_recent_unfinished_capture_is_left_to_its_writer(self):
        log, indexer = self.live()
        self.feed(log, indexer, [reception(T0, seq=1)])
        store = self.store()
        self.assertEqual(ddb.sync_captures(store, self.captures), [])  # still open, written seconds ago
        indexer.close(); log.close("requested")
        self.assertEqual(len(ddb.sync_captures(store, self.captures)), 1)

    def test_database_failure_never_loses_captured_records(self):
        class Failing(ddb.Store):
            calls = 0
            def ingest_batch(self, *a):
                Failing.calls += 1
                if Failing.calls > 1:
                    raise sqlite3.OperationalError("disk I/O error")
                return super().ingest_batch(*a)
        log, indexer = self.live(store_factory=Failing)
        self.feed(log, indexer, [reception(T0 + i, seq=i) for i in range(1, 6)])
        time.sleep(0.3)
        self.feed(log, indexer, [reception(T0 + i, seq=i) for i in range(6, 21)])
        time.sleep(0.3)
        indexer.close(); log.close("requested")
        self.assertEqual(indexer.state, "FAILED")
        self.assertEqual(indexer.indexed + indexer.dropped, 20)
        found = cl.read_capture(log.path)
        self.assertTrue(found["complete"])
        self.assertEqual(sum(1 for r in found["records"] if r["type"] == "bridge" and r["layer"] == "decoded_remote_id"), 20)
        self.assertEqual([r["error"] for r in found["records"] if r["type"] == "db_error"], ["OperationalError"])
        store = self.store()
        self.assertEqual(store.db.execute("SELECT needs_reimport FROM sources").fetchone()[0], 1)
        os.utime(log.path, (time.time() - 60, time.time() - 60))
        ddb.sync_captures(store, self.captures)
        self.assertEqual(store.search("SYNTH-ONE")[0]["reception_count"], 20)

    def test_unopenable_database_is_reported_and_capture_continues(self):
        (self.dir / "dronk.sqlite3").mkdir(parents=True)  # a directory where the database should be
        log, indexer = self.live()
        self.feed(log, indexer, [reception(T0, seq=1)])
        indexer.close(); log.close("requested")
        self.assertEqual(indexer.state, "FAILED")
        self.assertTrue(cl.read_capture(log.path)["complete"])

    def test_full_queue_drops_index_entries_only(self):
        gate = __import__("threading").Event()
        class Slow(ddb.Store):
            def ingest_batch(self, *a):
                gate.wait(5)
                return super().ingest_batch(*a)
        log, indexer = self.live(store_factory=Slow, maxsize=2)
        time.sleep(0.2)
        self.feed(log, indexer, [reception(T0 + i, seq=i) for i in range(1, 31)])
        self.assertGreater(indexer.dropped, 0)  # offer() never blocked the caller
        gate.set()
        indexer.close(); log.close("requested")
        store = self.store()
        self.assertEqual(store.db.execute("SELECT needs_reimport FROM sources").fetchone()[0], 1)
        os.utime(log.path, (time.time() - 60, time.time() - 60))
        ddb.sync_captures(store, self.captures)
        self.assertEqual(store.search("SYNTH-ONE")[0]["reception_count"], 30)


class RestartRecoveryTests(PanelStateCase):
    def test_restored_aircraft_are_stale_until_heard_again(self):
        db = Path(self.test_home) / "dronk.sqlite3"
        store = ddb.Store(db); self.addCleanup(store.close)
        log = cl.CaptureLog(Path(self.test_home) / "captures", "CAPTURE", ["front"], disk_usage=lambda _p: SimpleNamespace(free=10 * 2**30))
        for i in range(1, 4):
            log.bridge(reception(int(self.now * 1000) - 5000 + i, seq=i))
        log.close("requested")
        store.import_file(log.path)
        self.state.restore(store.restore_detections())
        d, = self.state.snapshot()["detections"]
        self.assertEqual((d["state"], d["observation_count"], d["position_valid"], d["last_position"]), ("STALE", 3, False, None))
        self.state.apply(reception(int(self.now * 1000), seq=9))
        d, = self.state.snapshot()["detections"]
        self.assertEqual((d["state"], d["observation_count"]), ("DETECTED_NO_POSITION", 4))

    def test_console_restart_restores_and_serves_history(self):
        config = dict(db_path=str(Path(self.test_home) / "dronk.sqlite3"), capture_dir=str(Path(self.test_home) / "captures"))
        log = cl.CaptureLog(config["capture_dir"], "CAPTURE", ["front"], disk_usage=lambda _p: SimpleNamespace(free=10 * 2**30))
        log.bridge(reception(int(self.now * 1000) - 1000, seq=1)); log.close("requested")
        os.utime(log.path, (time.time() - 60, time.time() - 60))
        state = cc.State()
        server = cc.make_server(state, 0)
        self.addCleanup(server.server_close)
        database = cc.Database(state, server, config)
        database.open()  # empty database: the background sync imports the capture, then restores
        self.addCleanup(lambda: server.store and server.store.close())
        deadline = time.monotonic() + 10
        while not state.snapshot()["detections"] and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(state.snapshot()["detections"][0]["state"], "STALE")
        self.assertEqual(state.snapshot()["db"]["state"], "READY")
        with server.store_lock:
            self.assertEqual(server.store.search("SYNTH")[0]["reception_count"], 1)


class WorkerIndexTests(IsolatedConfigTestCase):
    """Real worker + bridge + pseudo-terminal receiver, with the live index enabled."""
    def test_real_bridge_reception_is_captured_and_indexed_live(self):
        from test_scan_coverage import PROCESSOR, RECEPTION
        master, slave = os.openpty()
        self.addCleanup(os.close, master); self.addCleanup(os.close, slave)
        db = self.test_home / "state" / "dronk.sqlite3"
        state = cc.State()
        control = cc.HostController(state, dict(demo=False, devices={"front": os.ttyname(slave)}, expected={}, processor=str(PROCESSOR),
                                                forward_live=False, duration=0, mode="CAPTURE",
                                                capture_dir=str(self.test_home / "captures"), db_path=str(db)))
        self.addCleanup(control.stop)
        control.start()
        deadline = time.monotonic() + 20
        while not any(r.get("connected") for r in state.snapshot()["receivers"]) and time.monotonic() < deadline:
            time.sleep(0.05)
        os.write(master, RECEPTION.read_bytes())
        while ((state.snapshot()["capture"] or {}).get("index") or {}).get("indexed") != 1 and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(state.snapshot()["capture"]["index"]["state"], "INDEXING")
        control.stop()
        self.assertEqual(state.snapshot()["capture"]["index"]["state"], "CLOSED")
        store = ddb.Store(db); self.addCleanup(store.close)
        a, = store.search("SYNTH-C5-TEST")
        self.assertEqual((a["reception_count"], a["positioned_count"]), (1, 0))
        row = store.db.execute("SELECT dedup_key, receiver_label, position_valid, latitude FROM receptions").fetchone()
        self.assertEqual(tuple(row), ("rx:" + "a" * 32 + ":4", "front", 0, None))
