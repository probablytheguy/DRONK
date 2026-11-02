#!/usr/bin/env python3
"""DRONK SQLite store: normalized Remote ID receptions, aircraft identities, derived sightings, and
(Phase 2) capture-quality evidence with derived coverage, losses and sighting quality.

Capture files (JSONL, CAPTURE.md) are authoritative; this database is derived from them and can be
rebuilt at any time. Raw receiver lines are never stored here. Imported files are first copied into a
private imports directory, so a rebuild can always reproduce them. See docs/DRONK-PHASE1-SQLITE.md and
docs/DRONK-PHASE2-ANALYSIS.md."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import sqlite3
import stat
import sys
import threading
import time
import uuid

from capture_log import private_directory
import dronk_quality as quality

MIGRATIONS = Path(__file__).with_name("migrations")
GAP_SECONDS = 300
DERIVATION = "gap-v1"
BATCH = 500
# OpenDroneID Basic ID id_type; 1 and 2 match the shared core's IdentifierType names.
ODID_ID_TYPES = {1: "SERIAL_NUMBER", 2: "CAA_REGISTRATION_ID", 3: "UTM_ASSIGNED_UUID", 4: "SPECIFIC_SESSION_ID"}
KINDS = {"rid_reception": "rid_reception", "observation": "observation", "reception": "tracker_ignored"}
SEQ_MAX = 2**63 - 1
INDEX_VERSION = 2  # sources indexed with byte offsets, capture events and receiver lines
EVENT_TYPES = {"capture_start", "usb", "diagnostics", "receiver_session", "capture_suspended", "db_error", "capture_end"}
IMPORT_MAX_BYTES = 2 * 2**30
IMPORT_MAX_FILES = 50
IMPORT_MIN_FREE_BYTES = 512 * 2**20
PARTIAL_MAX_AGE = 3600
# name: (default, minimum, maximum). Each derived row records the values it was computed with.
ANALYSIS = dict(sighting_gap_seconds=(GAP_SECONDS, 30, 86400), packet_gap_seconds=(quality.PACKET_GAP_SECONDS, 1, 3600),
                edge_seconds=(quality.EDGE_SECONDS, 0, 3600), status_silence_seconds=(quality.SILENCE_SECONDS, 6, 3600))


class StoreError(Exception):
    pass


class Rejected(ValueError):
    pass


def default_path(environ=None):
    env = os.environ if environ is None else environ
    return Path(env.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "dronk" / "dronk.sqlite3"


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def epoch_ms(value):
    if not isinstance(value, str):
        raise Rejected("timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise Rejected("timestamp") from None
    if parsed.tzinfo is None:
        raise Rejected("timestamp")
    return int(parsed.timestamp() * 1000)


def iso_ms(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical(event):
    return json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def reception_identity(r):
    """Identity only when the receiver withheld the track for lack of position alone: exactly one
    textual Basic ID of a known type. no_basic_id/no_textual_id/ambiguous_id never become aircraft."""
    ids = {(b.get("id_type"), b.get("value")) for b in r.get("basic_ids") or [] if isinstance(b, dict) and b.get("value")}
    if r.get("reason") != "no_valid_position" or len(ids) != 1:
        return None
    id_type, value = ids.pop()
    return (ODID_ID_TYPES[id_type], value) if id_type in ODID_ID_TYPES and isinstance(value, str) else None


def _int(value, name, low=None, high=None, optional=True):
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or (low is not None and value < low) or (high is not None and value > high):
        raise Rejected(name)
    return value


def normalize(event):
    """Bridge event -> reception row. Raises Rejected for anything that isn't a well-formed Remote ID record."""
    if not isinstance(event, dict) or event.get("kind") not in KINDS:
        raise Rejected("kind")
    kind = KINDS[event["kind"]]
    if kind == "rid_reception":
        r = event.get("reception")
        if not isinstance(r, dict):
            raise Rejected("shape")
        identity, position, rf, reason = reception_identity(r), None, r, r.get("reason")
        messages = r.get("messages")
        if not isinstance(messages, list) or not all(isinstance(m, str) for m in messages):
            raise Rejected("messages")
        synthetic, received_at, observed_at = r.get("synthetic"), r.get("received_at"), r.get("observed_at")
        session, seq, label = r.get("session_id"), r.get("seq"), r.get("receiver_id") or r.get("stream")
        odid = r.get("odid_protocol_version")
    else:
        a = event.get("aircraft")
        if not isinstance(a, dict) or not isinstance(a.get("id"), str) or not a["id"]:
            raise Rejected("shape")
        rf = a.get("rf") or {}
        if not isinstance(rf, dict):
            raise Rejected("shape")
        identity = (a.get("identifier_type") or "UNKNOWN", a["id"])
        position, messages = a.get("position"), None
        reason = event.get("reason") if kind == "tracker_ignored" else None
        synthetic, received_at, observed_at = a.get("synthetic"), a.get("received_at"), a.get("observed_at")
        session, seq, label, odid = rf.get("session_id"), rf.get("seq"), rf.get("receiver_id") or rf.get("stream"), None
    if not isinstance(synthetic, bool):
        raise Rejected("synthetic")
    received_ms = epoch_ms(received_at)
    if observed_at is not None:
        epoch_ms(observed_at)
    valid = isinstance(position, dict) and position.get("latitude") is not None and position.get("longitude") is not None
    if valid:
        lat, lon, alt = position["latitude"], position["longitude"], position.get("altitude_hae_m")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (lat, lon)) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise Rejected("position")
        if alt is not None and (isinstance(alt, bool) or not isinstance(alt, (int, float))):
            raise Rejected("position")
    else:
        lat = lon = alt = None
    if (session is None) != (seq is None):
        raise Rejected("provenance")
    if session is not None:
        if not isinstance(session, str) or not 8 <= len(session) <= 64:
            raise Rejected("provenance")
        try:
            seq = int(seq) if isinstance(seq, (str, int)) and not isinstance(seq, bool) else None
        except ValueError:
            seq = None
        if seq is None or not 0 <= seq <= SEQ_MAX:
            raise Rejected("provenance")
    if label is not None and not isinstance(label, str):
        raise Rejected("receiver")
    payload = canonical(event)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return dict(
        key=f"rx:{session}:{seq}" if session else "legacy:" + digest, payload_sha256=digest, session_id=session, seq=seq,
        record_kind=kind, identity=identity, received_at=received_at, received_ms=received_ms, observed_at=observed_at,
        receiver_label=label, transport=rf.get("transport"), channel=_int(rf.get("channel"), "channel", 1, 196),
        frequency_mhz=_int(rf.get("frequency_mhz"), "frequency", 2000, 7200), rssi_dbm=_int(rf.get("rssi_dbm"), "rssi", -128, 20),
        transmitter=rf.get("transmitter"), message_counter=_int(rf.get("message_counter"), "message_counter", 0, 255),
        odid_version=_int(odid, "odid_version", 0, 15), messages=" ".join(messages) if messages is not None else None,
        reason=reason, position_valid=int(valid), latitude=lat, longitude=lon, altitude_hae_m=alt,
        synthetic=int(synthetic), payload_json=payload)


class Store:
    """One connection. The live indexer, the console and the CLI each open their own."""

    def __init__(self, path, synthetic=False, gap_seconds=None, migrations=MIGRATIONS, create=True, packet_gap_seconds=None,
                 edge_seconds=None, silence_seconds=None, imports_dir=None, capture_dir=None):
        """Analysis parameters left as None keep the values stored in the database (or the defaults), so the
        live indexer and the CLI follow whatever the console was configured with."""
        self.path, self.synthetic = Path(path), bool(synthetic)
        self.imports = Path(imports_dir) if imports_dir else self.path.parent / "imports"
        self.captures = Path(capture_dir) if capture_dir else None
        requested = dict(sighting_gap_seconds=gap_seconds, packet_gap_seconds=packet_gap_seconds, edge_seconds=edge_seconds,
                         status_silence_seconds=silence_seconds)
        for name, value in requested.items():
            low, high = ANALYSIS[name][1:]
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high):
                raise StoreError(f"{name} must be {low}–{high}")
        if not self.path.exists() and not create:
            raise StoreError("Database does not exist")
        private_directory(self.path.parent)
        if not self.path.exists():
            os.close(os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        self.db = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False)
        try:
            self._open(migrations, requested)
        except BaseException:
            self.db.close()  # a refused or failed open leaves no connection behind
            raise

    def _open(self, migrations, requested):
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA busy_timeout = 10000")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.migrate(migrations)
        stored = {k: int(v) for k, v in self.db.execute("SELECT key, value FROM settings") if k in ANALYSIS}
        self.analysis = {k: requested[k] if requested[k] is not None else stored.get(k, ANALYSIS[k][0]) for k in ANALYSIS}
        if self.analysis["packet_gap_seconds"] >= self.analysis["sighting_gap_seconds"]:
            raise StoreError("The packet-gap quality threshold must be shorter than the sighting gap")
        self.gap = self.analysis["sighting_gap_seconds"]
        with self.transaction():
            if stored != self.analysis:
                self.db.executemany("INSERT OR REPLACE INTO settings VALUES (?, ?)", [(k, str(v)) for k, v in self.analysis.items()])
            self.recompute()

    @property
    def thresholds(self):
        return dict(packet_gap_seconds=self.analysis["packet_gap_seconds"], edge_seconds=self.analysis["edge_seconds"],
                    silence_seconds=self.analysis["status_silence_seconds"])

    def recompute(self):
        """Bring derived rows computed with other parameters or algorithm versions up to date (database only)."""
        silence = self.analysis["status_silence_seconds"]
        for row in self.db.execute("SELECT DISTINCT aircraft_id FROM sightings WHERE gap_seconds != ? OR derivation != ?",
                                   (self.gap, DERIVATION)).fetchall():
            self.rebuild_sightings(row[0])
        sources = self.db.execute("SELECT DISTINCT source_id FROM coverage WHERE silence_seconds != ? OR derivation != ?",
                                  (silence, quality.QUALITY)).fetchall()
        for row in sources:
            quality.derive_source(self.db, row[0], silence)
        t = self.thresholds
        where = "quality_derivation IS NULL OR quality_derivation != ? OR packet_gap_seconds != ? OR edge_seconds != ? OR silence_seconds != ?"
        params = (quality.QUALITY, t["packet_gap_seconds"], t["edge_seconds"], t["silence_seconds"])
        quality.refresh(self.db, "1" if sources else where, () if sources else params, **t)

    def close(self):
        self.db.close()

    # -- schema --------------------------------------------------------------------------------
    def migrate(self, directory):
        files = sorted(Path(directory).glob("[0-9][0-9][0-9][0-9]_*.sql"))
        latest = int(files[-1].name[:4]) if files else 0
        current = self.db.execute("PRAGMA user_version").fetchone()[0]
        if current > latest:
            raise StoreError(f"Database schema v{current} is newer than this DRONK (v{latest}); not opened")
        pending = [f for f in files if int(f.name[:4]) > current]
        if not pending:
            return
        if current > 0:
            self.backup(f"v{current}")
        for f in pending:
            version = int(f.name[:4])
            self.db.execute("BEGIN IMMEDIATE")
            try:
                if self.db.execute("PRAGMA user_version").fetchone()[0] >= version:  # another process got there first
                    self.db.execute("ROLLBACK")
                    continue
                for statement in _statements(f.read_text()):
                    self.db.execute(statement)
                self.db.execute("INSERT INTO schema_migrations VALUES (?, ?, ?)", (version, f.stem[5:], utc_now()))
                self.db.execute(f"PRAGMA user_version = {version}")
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def backup(self, label):
        """Online copy with the SQLite backup API; never deleted automatically."""
        target = self.path.with_name(f"{self.path.name}.bak-{label}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')}")
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        copy = sqlite3.connect(target)
        try:
            self.db.backup(copy)
            # A backup that can't be read back is not a backup: stop before migrating.
            if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or \
                    copy.execute("PRAGMA user_version").fetchone()[0] != self.schema_version():
                raise StoreError(f"Backup {target.name} failed verification; database not migrated")
        finally:
            copy.close()
        return target

    def schema_version(self):
        return self.db.execute("PRAGMA user_version").fetchone()[0]

    def transaction(self):
        return _Transaction(self)

    # -- sources -------------------------------------------------------------------------------
    def capture_source(self, header, file_name, sha256=None, stored_name=None, file_bytes=None):
        if bool(header.get("synthetic")) != self.synthetic:
            raise StoreError("Synthetic and field captures use separate databases")
        row = self.db.execute("SELECT id FROM sources WHERE capture_id = ?", (header["capture_id"],)).fetchone()
        if row:
            if sha256:
                # A capture is append-only, so a later or longer copy of it holds every record of an earlier one.
                self.db.execute("UPDATE sources SET file_sha256 = ?, file_bytes = ?, stored_name = coalesce(?, stored_name) WHERE id = ?",
                                (sha256, file_bytes, stored_name, row[0]))
            return row[0]
        return self.db.execute(
            "INSERT INTO sources (kind, file_name, file_sha256, capture_id, mode, synthetic, started_at, imported_at, stored_name, file_bytes) "
            "VALUES ('capture', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (file_name, sha256, header["capture_id"], header.get("mode"), int(bool(header.get("synthetic"))), header.get("t"), utc_now(),
             stored_name, file_bytes)).lastrowid

    def finish_source(self, source_id, **fields):
        allowed = {"ended_at", "complete", "truncated_tail", "malformed_lines", "last_record_n", "needs_reimport", "index_version"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if fields:
            self.db.execute(f"UPDATE sources SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?", (*fields.values(), source_id))

    # -- ingest --------------------------------------------------------------------------------
    def ingest(self, source_id, record_n, record_t, event, offset=None):
        """One record inside the caller's transaction. Returns accepted / duplicate / already_imported /
        conflict (accepted under a disambiguated key) / rejected:<reason>. Re-importing backfills offsets."""
        if isinstance(event, dict) and event.get("kind") == "marker_state":
            return self._marker(source_id, record_n, event, offset)
        if self.db.execute("SELECT 1 FROM reception_sources WHERE source_id = ? AND record_n = ?", (source_id, record_n)).fetchone():
            if offset is not None:
                self.db.execute("UPDATE reception_sources SET byte_offset = ? WHERE source_id = ? AND record_n = ? AND byte_offset IS NULL",
                                (offset, source_id, record_n))
            return "already_imported"
        try:
            row = normalize(event)
        except Rejected as reason:
            return f"rejected:{reason}"
        if bool(row["synthetic"]) != self.synthetic:
            return "rejected:synthetic"
        status = "accepted"
        existing = self.db.execute("SELECT id, payload_sha256 FROM receptions WHERE dedup_key = ?", (row["key"],)).fetchone()
        if existing and existing["payload_sha256"] != row["payload_sha256"]:
            # Same receiver key, different content: a reused session ID or a corrupted copy. Never merge.
            row["key"] += "#" + row["payload_sha256"][:16]
            existing = self.db.execute("SELECT id, payload_sha256 FROM receptions WHERE dedup_key = ?", (row["key"],)).fetchone()
            status = "conflict"
        if existing:
            reception_id, status = existing["id"], "duplicate"
        else:
            reception_id = self._insert(row)
        self.db.execute("INSERT INTO reception_sources (reception_id, source_id, record_n, record_t, byte_offset) VALUES (?, ?, ?, ?, ?)",
                        (reception_id, source_id, record_n, record_t, offset))
        return status

    def _marker(self, source_id, record_n, e, offset):
        """A bridge marker transition (LIVE / LAST_KNOWN); idempotent per capture record."""
        try:
            position = e.get("position") or {}
            lat, lon, alt = position.get("latitude"), position.get("longitude"), position.get("altitude_hae_m")
            if (lat is None) != (lon is None) or e.get("role") not in ("aircraft", "operator", "takeoff") or e.get("state") not in ("LIVE", "LAST_KNOWN"):
                raise Rejected("marker")
            if not isinstance(e.get("synthetic"), bool) or e["synthetic"] != self.synthetic:
                raise Rejected("synthetic")
            ms = lambda name: epoch_ms(e[name]) if e.get(name) is not None else None
            aircraft = self.db.execute("SELECT id FROM aircraft WHERE identifier_type = ? AND identifier = ? AND synthetic = ?",
                                       (e["identifier_type"], e["id"], int(self.synthetic))).fetchone()
            inserted = self.db.execute(
                "INSERT OR IGNORE INTO marker_states VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (source_id, record_n, offset, aircraft[0] if aircraft else None, e["identifier_type"], e["id"], e["uid"], e["role"], e["state"],
                 str(e.get("reason")), epoch_ms(e["last_seen"]), ms("reported_at"), ms("stale_at"), e.get("stream"), lat, lon, alt,
                 int(self.synthetic))).rowcount
        except (KeyError, TypeError, Rejected) as reason:
            return f"rejected:{reason if isinstance(reason, Rejected) else 'marker'}"
        return "marker_state" if inserted else "already_imported"

    def _insert(self, row):
        session_ref = None
        if row["session_id"]:
            self.db.execute(
                "INSERT INTO receiver_sessions (session_id, receiver_label, first_ms, last_ms) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET first_ms = min(first_ms, excluded.first_ms), last_ms = max(last_ms, excluded.last_ms)",
                (row["session_id"], row["receiver_label"] or "unknown", row["received_ms"], row["received_ms"]))
            session_ref = self.db.execute("SELECT id FROM receiver_sessions WHERE session_id = ?", (row["session_id"],)).fetchone()[0]
        aircraft_id = None
        if row["identity"]:
            id_type, identifier = row["identity"]
            ms, at = row["received_ms"], row["received_at"]
            self.db.execute(
                "INSERT INTO aircraft (identifier_type, identifier, synthetic, first_ms, last_ms, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING", (id_type, identifier, row["synthetic"], ms, ms, at, at))
            aircraft_id = self.db.execute("SELECT id FROM aircraft WHERE identifier_type = ? AND identifier = ? AND synthetic = ?",
                                          (id_type, identifier, row["synthetic"])).fetchone()[0]
            self.db.execute(
                "UPDATE aircraft SET reception_count = reception_count + 1, positioned_count = positioned_count + ?, "
                "first_seen = CASE WHEN ? < first_ms THEN ? ELSE first_seen END, first_ms = min(first_ms, ?), "
                "last_seen = CASE WHEN ? > last_ms THEN ? ELSE last_seen END, last_ms = max(last_ms, ?) WHERE id = ?",
                (row["position_valid"], ms, at, ms, ms, at, ms, aircraft_id))
            self.dirty[aircraft_id] = min(self.dirty.get(aircraft_id, ms), ms)
        return self.db.execute(
            "INSERT INTO receptions (dedup_key, payload_sha256, receiver_session_id, seq, record_kind, aircraft_id, received_at, "
            "received_ms, observed_at, receiver_label, transport, channel, frequency_mhz, rssi_dbm, transmitter, message_counter, "
            "odid_version, messages, reason, position_valid, latitude, longitude, altitude_hae_m, synthetic, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row["key"], row["payload_sha256"], session_ref, row["seq"], row["record_kind"], aircraft_id, row["received_at"],
             row["received_ms"], row["observed_at"], row["receiver_label"], row["transport"], row["channel"], row["frequency_mhz"],
             row["rssi_dbm"], row["transmitter"], row["message_counter"], row["odid_version"], row["messages"], row["reason"],
             row["position_valid"], row["latitude"], row["longitude"], row["altitude_hae_m"], row["synthetic"], row["payload_json"])).lastrowid

    dirty = None  # aircraft_id -> earliest new received_ms in the open transaction (sightings to update)

    def ingest_batch(self, source_id, items, lines=(), events=()):
        """[(record_n, record_t, event, byte_offset)] in one transaction, sightings updated before commit;
        receiver_lines and capture_events rows (already shaped) go into the same transaction."""
        results = Counter()
        with self.transaction():
            for n, t, event, offset in items:
                results[self.ingest(source_id, n, t, event, offset)] += 1
            self.db.executemany("INSERT OR IGNORE INTO receiver_lines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                [(source_id, *line) for line in lines])
            self.db.executemany("INSERT OR IGNORE INTO capture_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                [(source_id, *event) for event in events])
        return results

    # -- sightings (derived) -------------------------------------------------------------------
    def rebuild_sightings(self, aircraft_id, from_ms=None):
        """Recompute windows from the first one that a reception at from_ms could touch onward."""
        gap_ms = self.gap * 1000
        start = None
        if from_ms is not None:
            start = self.db.execute("SELECT min(start_ms) FROM sightings WHERE aircraft_id = ? AND end_ms >= ?",
                                    (aircraft_id, from_ms - gap_ms)).fetchone()[0]
            start = from_ms if start is None else min(start, from_ms)
            self.db.execute("DELETE FROM sightings WHERE aircraft_id = ? AND end_ms >= ?", (aircraft_id, from_ms - gap_ms))
        else:
            self.db.execute("DELETE FROM sightings WHERE aircraft_id = ?", (aircraft_id,))
        rows = self.db.execute("SELECT id, received_ms, position_valid, receiver_label FROM receptions "
                               "WHERE aircraft_id = ? AND received_ms >= ? ORDER BY received_ms, id",
                               (aircraft_id, start if start is not None else -2**62)).fetchall()
        window = None
        def flush():
            if window:
                fields = quality.sighting_quality(self.db, dict(start_ms=window["start"], end_ms=window["end"]), window["times"], **self.thresholds)
                self.db.execute(f"INSERT INTO sightings (aircraft_id, gap_seconds, derivation, start_ms, end_ms, reception_count, "
                                f"positioned_count, receivers, first_reception_id, last_reception_id, {', '.join(fields)}) "
                                f"VALUES ({', '.join('?' * (10 + len(fields)))})",
                                (aircraft_id, self.gap, DERIVATION, window["start"], window["end"], window["count"], window["positioned"],
                                 " ".join(sorted(window["receivers"])), window["first"], window["last"], *fields.values()))
        for r in rows:
            if window is None or r["received_ms"] - window["end"] > gap_ms:
                flush()
                window = dict(start=r["received_ms"], end=r["received_ms"], count=0, positioned=0, receivers=set(), first=r["id"], last=r["id"], times=[])
            window["times"].append(r["received_ms"])
            window.update(end=r["received_ms"], last=r["id"], count=window["count"] + 1, positioned=window["positioned"] + r["position_valid"])
            if r["receiver_label"]:
                window["receivers"].add(r["receiver_label"])
        flush()

    # -- file import ---------------------------------------------------------------------------
    def stored(self, path):
        """Already in the private imports directory or the capture directory: imported in place."""
        parent = Path(path).resolve().parent
        return parent == self.imports.resolve() or (self.captures is not None and parent == self.captures.resolve())

    disk_usage = staticmethod(shutil.disk_usage)

    def import_files(self, paths):
        """Import a batch. Limits and aggregate free space are checked before anything is copied; each file is
        copied into the private imports directory (unless already in captures/ or imports/) and imported from
        that verified copy. A failing file is reported and does not stop the others."""
        paths = [Path(p) for p in paths]
        if len(paths) > IMPORT_MAX_FILES:
            raise StoreError(f"At most {IMPORT_MAX_FILES} files per import batch")
        planned, needed = [], 0
        for path in paths:
            try:
                info = path.stat()
                if not stat.S_ISREG(info.st_mode):
                    raise StoreError("not a regular file")
                if info.st_size > IMPORT_MAX_BYTES:
                    raise StoreError("larger than the 2 GiB per-file import limit")
                if info.st_size == 0:
                    raise StoreError("empty file")
                copy = not self.stored(path)
                needed += info.st_size if copy else 0
                planned.append((path, copy))
            except (StoreError, OSError) as error:
                planned.append((path, error))
        if needed:
            private_directory(self.imports)
            self.clean_partials()
            if self.disk_usage(self.imports).free - needed < IMPORT_MIN_FREE_BYTES:
                raise StoreError("Not enough free disk space to copy this import batch; nothing imported")
        results = []
        for path, plan in planned:
            try:
                if isinstance(plan, Exception):
                    raise plan
                results.append(self.import_file(self.stage(path) if plan else path, original_name=path.name))
            except (StoreError, OSError) as error:
                results.append(dict(file=path.name, error=_reason(error)))
        return results

    def stage(self, path):
        """Copy into imports/ as a verified read-only file named by its SHA-256 prefix; returns the stored path.
        Identical content is stored once. An interrupted or changing copy leaves nothing behind but a
        .partial file, which is removed here or by a later clean-up."""
        path = Path(path)
        private_directory(self.imports)
        before = path.stat()
        if _capture_in_progress(path, before):
            raise StoreError("capture still being written; import it after it closes")
        temp = self.imports / f".partial-{uuid.uuid4().hex}"
        digest, size = hashlib.sha256(), 0
        try:
            with path.open("rb") as source, open(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600), "wb") as out:
                for block in iter(lambda: source.read(1 << 20), b""):
                    size += len(block)
                    if size > IMPORT_MAX_BYTES:
                        raise StoreError("larger than the 2 GiB per-file import limit")
                    digest.update(block)
                    out.write(block)
                out.flush()
                os.fsync(out.fileno())
            after = path.stat()
            if size != before.st_size or (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                raise StoreError("file changed while it was being copied; nothing imported")
            digest = digest.hexdigest()
            for existing in self.imports.glob(digest[:16] + "-*"):
                if _sha256(existing) == digest:
                    return existing
            target = self.imports / f"{digest[:16]}-{_safe_name(path.name)}"
            if target.exists():  # a different file with the same prefix: use the full digest
                target = self.imports / f"{digest}-{_safe_name(path.name)}"
            os.chmod(temp, 0o400)
            os.link(temp, target)  # never replaces an existing file
            _fsync_directory(self.imports)
            return target
        finally:
            temp.unlink(missing_ok=True)

    def clean_partials(self, max_age=PARTIAL_MAX_AGE):
        """Remove copies abandoned by an interrupted import (old enough not to be another import in progress)."""
        for partial in self.imports.glob(".partial-*"):
            try:
                if time.time() - partial.stat().st_mtime > max_age:
                    partial.unlink()
            except OSError:
                pass

    def import_file(self, path, interrupt_after=None, original_name=None):
        """Import one capture JSONL or legacy dashboard snapshot in place. Idempotent; resumable after
        interruption; re-importing an indexed file backfills byte offsets, capture events and receiver lines."""
        path = Path(path)
        name = original_name or path.name
        stored_name = path.name if path.resolve().parent == self.imports.resolve() else None
        size = path.stat().st_size
        result = dict(file=name, kind=None, accepted=0, duplicate=0, already_imported=0, conflict=0, rejected=0,
                      rejected_reasons={}, other_records=0, receiver_lines=0, capture_events=0, marker_state=0, complete=None, truncated_tail=None,
                      malformed_lines=0, stored=stored_name is not None)
        digest = _sha256(path)
        with path.open("rb") as handle:
            first = handle.readline()
        try:
            header = json.loads(first)
        except ValueError:
            header = None
        if isinstance(header, dict) and header.get("type") == "capture_start" and header.get("schema") == "dronk.capture":
            result["kind"] = "capture"
            records = _capture_records(path, header["capture_id"], result)
            with self.transaction():
                source = self.capture_source(header, name, digest, stored_name, size)
        else:
            result["kind"] = "dashboard_state"
            records = [("decoded", n, t, event, None) for n, t, event in _snapshot_records(path, result)]
            with self.transaction():
                row = self.db.execute("SELECT id FROM sources WHERE file_sha256 = ? AND capture_id IS NULL", (digest,)).fetchone()
                if row:
                    source = row[0]
                    self.db.execute("UPDATE sources SET stored_name = coalesce(?, stored_name), file_bytes = ? WHERE id = ?",
                                    (stored_name, size, source))
                else:
                    source = self.db.execute(
                        "INSERT INTO sources (kind, file_name, file_sha256, synthetic, imported_at, stored_name, file_bytes) "
                        "VALUES ('dashboard_state', ?, ?, ?, ?, ?, ?)", (name, digest, int(self.synthetic), utc_now(), stored_name, size)).lastrowid
        reasons, chunk, done, last_n = Counter(), ([], [], []), 0, None
        for kind, n, t, item, offset in records:
            last_n = n
            if kind == "decoded":
                chunk[0].append((n, t, item, offset))
            else:
                chunk[1 if kind == "line" else 2].append(item)
            if sum(map(len, chunk)) >= BATCH:
                done = self._commit(source, chunk, result, reasons, done, interrupt_after)
                chunk = ([], [], [])
        if any(chunk):
            self._commit(source, chunk, result, reasons, done, interrupt_after)
        result["rejected_reasons"] = dict(reasons)
        with self.transaction():
            fields = dict(truncated_tail=int(bool(result["truncated_tail"])), malformed_lines=result["malformed_lines"],
                          last_record_n=last_n, index_version=INDEX_VERSION)
            if result["kind"] == "capture":
                fields.update(complete=int(bool(result["complete"])), ended_at=result.get("ended_at"), needs_reimport=0)
            self.finish_source(source, **fields)
            span = quality.derive_source(self.db, source, self.analysis["status_silence_seconds"])
            if span:  # sightings whose coverage this source may change
                quality.refresh(self.db, "end_ms >= ? AND start_ms <= ?", span, **self.thresholds)
        return result

    def _commit(self, source, chunk, result, reasons, done, interrupt_after):
        batch, lines, events = chunk
        if interrupt_after is not None and done + len(batch) > interrupt_after:
            self._apply(source, (batch[:interrupt_after - done], lines, events), result, reasons)
            raise KeyboardInterrupt("simulated interruption")
        self._apply(source, chunk, result, reasons)
        return done + len(batch)

    def _apply(self, source, chunk, result, reasons):
        batch, lines, events = chunk
        for status, count in self.ingest_batch(source, batch, lines, events).items():
            if status.startswith("rejected:"):
                result["rejected"] += count; reasons[status[9:]] += count
            elif status == "conflict":
                result["conflict"] += count; result["accepted"] += count
            else:
                result[status] += count

    # -- queries -------------------------------------------------------------------------------
    def search(self, text="", limit=50):
        like = "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = self.db.execute(
            "SELECT a.*, (SELECT count(*) FROM sightings s WHERE s.aircraft_id = a.id) AS sightings FROM aircraft a "
            "WHERE a.synthetic = ? AND (a.identifier LIKE ? ESCAPE '\\') ORDER BY a.last_ms DESC LIMIT ?",
            (int(self.synthetic), like, max(1, min(int(limit), 500)))).fetchall()
        return [_aircraft(r) for r in rows]

    def aircraft_detail(self, identifier_type, identifier, before_ms=None, limit=100):
        row = self.db.execute(
            "SELECT a.*, (SELECT count(*) FROM sightings s WHERE s.aircraft_id = a.id) AS sightings FROM aircraft a "
            "WHERE identifier_type = ? AND identifier = ? AND synthetic = ?", (identifier_type, identifier, int(self.synthetic))).fetchone()
        if not row:
            return None
        limit = max(1, min(int(limit), 1000))
        timeline = self.db.execute(
            "SELECT r.*, (SELECT group_concat(s.file_name || '#' || p.record_n, ' ') FROM reception_sources p "
            "JOIN sources s ON s.id = p.source_id WHERE p.reception_id = r.id) AS provenance FROM receptions r "
            "WHERE aircraft_id = ? AND received_ms < ? ORDER BY received_ms DESC, id DESC LIMIT ?",
            (row["id"], before_ms if before_ms is not None else 2**62, limit)).fetchall()
        sightings = self.db.execute("SELECT * FROM sightings WHERE aircraft_id = ? ORDER BY start_ms DESC LIMIT 200", (row["id"],)).fetchall()
        return dict(aircraft=_aircraft(row),
                    sightings=[dict(start=iso_ms(s["start_ms"]), end=iso_ms(s["end_ms"]), duration_s=(s["end_ms"] - s["start_ms"]) / 1000,
                                    reception_count=s["reception_count"], positioned_count=s["positioned_count"],
                                    receivers=s["receivers"].split(), gap_seconds=s["gap_seconds"], derivation=s["derivation"])
                               for s in sightings],
                    timeline=[dict(received_at=r["received_at"], record_kind=r["record_kind"], receiver=r["receiver_label"],
                                   transport=r["transport"], channel=r["channel"], frequency_mhz=r["frequency_mhz"], rssi_dbm=r["rssi_dbm"],
                                   messages=r["messages"].split() if r["messages"] else None, reason=r["reason"],
                                   position_valid=bool(r["position_valid"]),
                                   position=dict(latitude=r["latitude"], longitude=r["longitude"], altitude_hae_m=r["altitude_hae_m"])
                                   if r["position_valid"] else None,
                                   provenance=(r["provenance"] or "").split(), received_ms=r["received_ms"]) for r in timeline])

    def restore_detections(self, limit=1024):
        """Latest state per aircraft for the console's detection list after a restart."""
        rows = self.db.execute(
            "SELECT a.*, r.receiver_label, r.transmitter, r.transport, r.channel, r.frequency_mhz, r.rssi_dbm, r.reason, r.position_valid, "
            "r.messages, (SELECT min(rssi_dbm) FROM receptions WHERE aircraft_id = a.id) AS rssi_min, "
            "(SELECT max(rssi_dbm) FROM receptions WHERE aircraft_id = a.id) AS rssi_max, "
            "(SELECT group_concat(DISTINCT receiver_label) FROM receptions WHERE aircraft_id = a.id) AS receivers, "
            "(SELECT id FROM receptions WHERE aircraft_id = a.id AND position_valid = 1 ORDER BY received_ms DESC LIMIT 1) AS fix "
            "FROM aircraft a JOIN receptions r ON r.id = (SELECT id FROM receptions WHERE aircraft_id = a.id ORDER BY received_ms DESC, id DESC LIMIT 1) "
            "WHERE a.synthetic = ? ORDER BY a.last_ms DESC LIMIT ?", (int(self.synthetic), limit)).fetchall()
        out = []
        for r in rows:
            fix = self.db.execute("SELECT latitude, longitude, altitude_hae_m, received_at FROM receptions WHERE id = ?", (r["fix"],)).fetchone() if r["fix"] else None
            messages = set()
            for m in self.db.execute("SELECT DISTINCT messages FROM receptions WHERE aircraft_id = ? AND messages IS NOT NULL", (r["id"],)):
                messages.update(m[0].split())
            out.append(dict(aircraft_id=r["identifier"], identifier_type=r["identifier_type"], synthetic=bool(r["synthetic"]),
                            first_seen=r["first_seen"], last_seen=r["last_seen"], observation_count=r["reception_count"],
                            receiver_ids=sorted((r["receivers"] or "").split(",")) if r["receivers"] else [], messages=sorted(messages),
                            rssi_min_dbm=r["rssi_min"], rssi_max_dbm=r["rssi_max"],
                            last_position=dict(latitude=fix["latitude"], longitude=fix["longitude"], altitude_hae_m=fix["altitude_hae_m"]) if fix else None,
                            last_position_at=fix["received_at"] if fix else None, uid=None, position_valid=bool(r["position_valid"]),
                            reason=r["reason"], receiver_id=r["receiver_label"], transmitter=r["transmitter"], transport=r["transport"],
                            channel=r["channel"], frequency_mhz=r["frequency_mhz"], rssi_dbm=r["rssi_dbm"], restored=True))
        return out

    def status(self):
        q = lambda sql: self.db.execute(sql).fetchone()[0]
        return dict(schema_version=self.schema_version(), aircraft=q("SELECT count(*) FROM aircraft"),
                    receptions=q("SELECT count(*) FROM receptions"), provenance=q("SELECT count(*) FROM reception_sources"),
                    sightings=q("SELECT count(*) FROM sightings"), sources=q("SELECT count(*) FROM sources"),
                    needs_reimport=q("SELECT count(*) FROM sources WHERE needs_reimport = 1"),
                    not_backfilled=q(f"SELECT count(*) FROM sources WHERE index_version < {INDEX_VERSION}"),
                    capture_events=q("SELECT count(*) FROM capture_events"), receiver_lines=q("SELECT count(*) FROM receiver_lines"),
                    coverage=q("SELECT count(*) FROM coverage"), known_losses=q("SELECT count(*) FROM known_losses"),
                    analysis=dict(self.analysis, sighting_derivation=DERIVATION, quality_derivation=quality.QUALITY))

    def check(self):
        """Integrity of the stored data and of the derived tables against it; ok only when every check passes."""
        q = lambda sql: self.db.execute(sql).fetchone()[0]
        problems = dict(
            integrity=[r[0] for r in self.db.execute("PRAGMA integrity_check") if r[0] != "ok"],
            foreign_keys=len(self.db.execute("PRAGMA foreign_key_check").fetchall()),
            aircraft_counts=q("SELECT count(*) FROM aircraft a WHERE reception_count != (SELECT count(*) FROM receptions WHERE aircraft_id = a.id) "
                              "OR positioned_count != (SELECT count(*) FROM receptions WHERE aircraft_id = a.id AND position_valid = 1) "
                              "OR first_ms != (SELECT min(received_ms) FROM receptions WHERE aircraft_id = a.id) "
                              "OR last_ms != (SELECT max(received_ms) FROM receptions WHERE aircraft_id = a.id)"),
            sighting_counts=q("SELECT count(*) FROM aircraft a WHERE reception_count != "
                              "(SELECT coalesce(sum(reception_count), 0) FROM sightings WHERE aircraft_id = a.id)"),
            receptions_without_provenance=q("SELECT count(*) FROM receptions r WHERE NOT EXISTS (SELECT 1 FROM reception_sources WHERE reception_id = r.id)"),
            sightings_without_quality=q("SELECT count(*) FROM sightings WHERE quality_derivation IS NULL"),
            capture_offsets_missing=q("SELECT count(*) FROM reception_sources p JOIN sources s ON s.id = p.source_id "
                                      "WHERE s.kind = 'capture' AND p.byte_offset IS NULL"))
        sources = [dict(id=r["id"], kind=r["kind"], file=r["file_name"], stored=r["stored_name"] is not None, complete=r["complete"],
                        index_version=r["index_version"]) for r in self.db.execute("SELECT * FROM sources ORDER BY id")]
        not_backfilled = [s for s in sources if s["index_version"] < INDEX_VERSION]
        unstored_legacy = [s for s in sources if s["kind"] == "dashboard_state" and not s["stored"]]
        ok = not problems["integrity"] and not any(v for k, v in problems.items() if k != "integrity")
        return dict(ok=ok and not not_backfilled, problems=problems, not_backfilled=not_backfilled,
                    legacy_without_stored_copy=unstored_legacy, status=self.status())

    def fingerprint(self):
        """Content summary for comparing two databases (e.g. incremental vs. rebuilt); ids are not compared."""
        keys = hashlib.sha256("\n".join(r[0] for r in self.db.execute("SELECT dedup_key FROM receptions ORDER BY dedup_key")).encode()).hexdigest()
        sightings = [list(r) for r in self.db.execute(
            "SELECT a.identifier_type, a.identifier, s.start_ms, s.end_ms, s.reception_count, s.positioned_count, s.coverage, s.open_start, "
            "s.open_end, s.long_gaps, s.max_gap_ms, s.coverage_gaps, s.disconnects, s.losses, s.possible_losses FROM sightings s "
            "JOIN aircraft a ON a.id = s.aircraft_id ORDER BY a.identifier_type, a.identifier, s.start_ms")]
        aircraft = [list(r) for r in self.db.execute("SELECT identifier_type, identifier, reception_count, positioned_count, first_ms, last_ms "
                                                     "FROM aircraft ORDER BY identifier_type, identifier")]
        coverage = [list(r) for r in self.db.execute("SELECT stream, start_ms, end_ms, end_reason, line_count FROM coverage ORDER BY start_ms, stream")]
        losses = [list(r) for r in self.db.execute("SELECT stream, start_ms, end_ms, kind, amount, certain FROM known_losses ORDER BY start_ms, kind")]
        markers = [list(r) for r in self.db.execute("SELECT uid, role, state, reason, last_seen_ms, reported_ms, latitude, longitude FROM marker_states "
                                                    "ORDER BY last_seen_ms, uid, reported_ms")]
        return dict(reception_keys=keys, receptions=self.db.execute("SELECT count(*) FROM receptions").fetchone()[0],
                    aircraft=aircraft, sightings=sightings, coverage=coverage, known_losses=losses, marker_states=markers)


class LiveIndexer(threading.Thread):
    """Indexes decoded records from the live capture into SQLite off the serial path.

    offer() never blocks: the queue is bounded, and a full queue, a database error or a closed indexer
    only drops the index entry (counted). The capture file still holds the record, and the console's
    sync imports the file afterwards, so nothing captured is lost."""
    def __init__(self, path, capture, synthetic=False, gap_seconds=None, maxsize=20000, interval=1.0, store_factory=None):
        super().__init__(name="dronk-index", daemon=True)
        self.path, self.capture, self.synthetic, self.gap, self.interval = path, capture, synthetic, gap_seconds, interval
        self.store_factory = store_factory or Store
        self.queue = queue.Queue(maxsize)
        self.state, self.error, self.indexed, self.dropped = "STARTING", None, 0, 0
        self.results = Counter()
        self.store = self.source = None

    def offer(self, n, t, offset, event):
        if self.state in ("FAILED", "CLOSED"):
            self.dropped += 1
            return
        try:
            self.queue.put_nowait((n, t, event, offset))
        except queue.Full:
            self.dropped += 1

    def run(self):
        try:
            self.store = self.store_factory(self.path, synthetic=self.synthetic, gap_seconds=self.gap)
            header = dict(capture_id=self.capture.capture_id, mode=self.capture.mode, synthetic=self.synthetic, t=self.capture.started_at)
            with self.store.transaction():
                self.source = self.store.capture_source(header, self.capture.path.name)
            self.state = "INDEXING"
        except Exception as error:
            return self.fail(error)
        while True:
            try:
                item = self.queue.get(timeout=self.interval)
            except queue.Empty:
                continue
            batch, done = [], False
            while True:  # one transaction per wake-up, at most BATCH records
                if item is None:
                    done = True
                    break
                batch.append(item)
                if len(batch) >= BATCH:
                    break
                try:
                    item = self.queue.get_nowait()
                except queue.Empty:
                    break
            if batch:
                try:
                    self.results.update(self.store.ingest_batch(self.source, batch))
                    self.indexed += len(batch)
                except Exception as error:
                    self.dropped += len(batch)
                    return self.fail(error)
            if done:
                return

    def fail(self, error):
        self.state, self.error = "FAILED", type(error).__name__
        self.capture.note(dict(type="db_error", error=self.error, detail=str(error)[:200], indexed=self.indexed))
        while True:  # whatever was queued is now an unindexed (still captured) record
            try:
                if self.queue.get_nowait() is not None:
                    self.dropped += 1
            except queue.Empty:
                break

    def close(self, timeout=15):
        if self.state not in ("FAILED", "CLOSED"):
            try:
                self.queue.put(None, timeout=timeout)
            except queue.Full:
                pass
        self.join(timeout)
        if self.store is not None:
            try:
                if self.source is not None:
                    with self.store.transaction():
                        self.store.finish_source(self.source, needs_reimport=int(bool(self.dropped) or self.state == "FAILED"))
            except Exception as error:
                if self.state != "FAILED":
                    self.state, self.error = "FAILED", type(error).__name__
            finally:
                self.store.close()
        if self.state != "FAILED":
            self.state = "CLOSED"

    def status(self):
        return dict(state=self.state, indexed=self.indexed, dropped=self.dropped, queued=self.queue.qsize(), error=self.error)


def sync_captures(store, directory, skip=()):
    """Import captures that live indexing didn't fully cover: unknown, flagged, or never file-verified."""
    results = []
    for path in sorted(Path(directory).glob("dronk-capture-*.jsonl")):
        try:
            if path.name in skip or (time.time() - path.stat().st_mtime < 10 and not _ended(path)):
                continue  # possibly still being written by a session that is just starting
            with path.open("rb") as f:
                header = json.loads(f.readline())
            row = store.db.execute("SELECT needs_reimport, file_sha256, complete, index_version FROM sources WHERE capture_id = ?",
                                   (header["capture_id"],)).fetchone()
        except (ValueError, KeyError, TypeError, OSError):
            continue
        # Interrupted captures (no capture_end) are re-scanned each time; that is idempotent. Sources indexed
        # before Phase 2 are re-scanned once to backfill offsets, capture events and receiver lines.
        if (row is None or row["needs_reimport"] or row["file_sha256"] is None or not row["complete"]
                or row["index_version"] < INDEX_VERSION):
            try:
                results.append(store.import_file(path))
            except (StoreError, OSError) as error:
                results.append(dict(file=path.name, error=str(error)))
    return results


class _Transaction:
    """BEGIN IMMEDIATE ... COMMIT. Sightings of aircraft that gained receptions are updated in the same
    transaction, so derived windows never disagree with committed receptions."""
    def __init__(self, store):
        self.store = store

    def __enter__(self):
        self.store.db.execute("BEGIN IMMEDIATE")
        self.store.dirty = {}
        return self

    def __exit__(self, kind, *_):
        try:
            if kind is None:
                for aircraft_id, from_ms in self.store.dirty.items():
                    self.store.rebuild_sightings(aircraft_id, from_ms)
                self.store.db.execute("COMMIT")
            else:
                self.store.db.execute("ROLLBACK")
        except BaseException:
            if self.store.db.in_transaction:
                self.store.db.execute("ROLLBACK")
            raise
        finally:
            self.store.dirty = None
        return False


def _statements(sql):
    """Migration files keep '--' comments and semicolons out of string literals, so this split is safe."""
    lines = [line.split("--", 1)[0] for line in sql.splitlines()]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def _ended(path):
    """The file's last line is a capture_end record: the writer has finished with it."""
    with Path(path).open("rb") as f:
        f.seek(max(0, f.seek(0, os.SEEK_END) - 8192))
        lines = f.read().rstrip(b"\n").split(b"\n")
    try:
        return json.loads(lines[-1]).get("type") == "capture_end"
    except (ValueError, AttributeError, IndexError):
        return False


def _sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _capture_records(path, capture_id, result):
    """Streams (kind, n, t, item, byte_offset) for decoded records, receiver lines and capture events;
    tolerant like capture_log.read_capture."""
    result.update(complete=False, truncated_tail=False)
    offset = 0
    with Path(path).open("rb") as f:
        for raw in f:
            at, offset = offset, offset + len(raw)
            if not raw.endswith(b"\n"):
                result["truncated_tail"] = True
                break
            try:
                record = json.loads(raw)
                if not isinstance(record, dict):
                    raise ValueError
            except ValueError:
                result["malformed_lines"] += 1
                continue
            if record.get("capture_id") != capture_id:
                result["malformed_lines"] += 1
                continue
            kind, n, t = record.get("type"), record.get("n"), record.get("t")
            if kind == "capture_end":
                result["complete"], result["ended_at"] = True, t
            if kind == "bridge" and record.get("layer") in ("decoded_remote_id", "marker"):
                yield "decoded", n, t, record.get("event"), at
                continue
            result["other_records"] += 1
            try:
                ms = epoch_ms(t)
            except Rejected:
                continue
            if not isinstance(n, int) or isinstance(n, bool):
                continue
            text = lambda name: record.get(name) if isinstance(record.get(name), str) else None
            if kind == "rx_line" and text("stream"):
                result["receiver_lines"] += 1
                yield "line", n, t, (n, at, ms, record["stream"], *_line_fields(record)), at
            elif kind in EVENT_TYPES:
                result["capture_events"] += 1
                detail = {k: v for k, v in record.items() if k not in ("capture_id", "n", "t", "type")}
                yield "event", n, t, (n, at, ms, kind, text("stream"), text("event") if kind == "usb" else None,
                                      text("session_id"), canonical(detail)), at


def _line_fields(record):
    """(line_type, session_id, seq, uptime_us, channel, counters) of a raw receiver line. The line itself,
    the receiver's factory ID and everything else in it stay in the capture only."""
    if record.get("truncated") or record.get("partial"):
        return "fragment", None, None, None, None, None
    line = record.get("line")
    if line == "":
        return "empty", None, None, None, None, None
    try:
        parsed = json.loads(line) if isinstance(line, str) else None
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict) or parsed.get("protocol") not in ("ouitak.receiver", "dronk.receiver") or not isinstance(parsed.get("type"), str):
        return "unparsed", None, None, None, None, None
    whole = lambda v: v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None
    session = parsed.get("session_id") if isinstance(parsed.get("session_id"), str) and 8 <= len(parsed["session_id"]) <= 64 else None
    seq = whole(parsed.get("seq"))
    counters = None
    if parsed["type"] == "status" and isinstance(parsed.get("counters"), dict):
        counters = canonical({k: v for k, v in parsed["counters"].items() if k in quality.LOSS_COUNTERS and whole(v) is not None})
    return (parsed["type"][:32], session, seq if seq is not None and seq <= SEQ_MAX else None, whole(parsed.get("uptime_us")),
            whole(parsed.get("channel")), counters)


def _capture_in_progress(path, info):
    try:
        with Path(path).open("rb") as f:
            header = json.loads(f.readline())
    except (ValueError, OSError):
        return False
    return (isinstance(header, dict) and header.get("type") == "capture_start" and time.time() - info.st_mtime < 10
            and not _ended(path))


def _safe_name(name):
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", name).lstrip(".")[:120]
    return cleaned or "file"


def _fsync_directory(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _reason(error):
    """User-facing reason without file system paths."""
    if isinstance(error, StoreError):
        return str(error)
    return getattr(error, "strerror", None) or type(error).__name__


def _snapshot_records(path, result):
    """Legacy (pre-Phase 0) dashboard /api/state snapshot: decoded events from its log ring, oldest first."""
    try:
        data = json.loads(Path(path).read_text())
        logs = data["logs"]
        if not isinstance(logs, list):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise StoreError(f"{Path(path).name}: not a DRONK capture or dashboard snapshot") from None
    records = []
    for index, entry in enumerate(reversed(logs)):
        if isinstance(entry, dict) and entry.get("kind") in KINDS:
            records.append((index, entry.get("time"), entry.get("event")))
        else:
            result["other_records"] += 1
    return records  # parsed before any source row is created


def _aircraft(r):
    return dict(identifier_type=r["identifier_type"], identifier=r["identifier"], synthetic=bool(r["synthetic"]),
                first_seen=r["first_seen"], last_seen=r["last_seen"], reception_count=r["reception_count"],
                positioned_count=r["positioned_count"], sightings=r["sightings"])


def main(argv=None):
    from capture_log import default_directory
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=os.environ.get("DRONK_DB") or str(default_path()))
    parser.add_argument("--captures", default=None, help="Capture directory (default: the standard capture directory)")
    parser.add_argument("--imports", default=None, help="Private imports directory (default: imports/ beside the database)")
    for name in ANALYSIS:
        parser.add_argument("--" + name.replace("_", "-"), type=int, default=None,
                            help=f"Default {ANALYSIS[name][0]}; when omitted, the value stored in the database is kept")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("import", help="Copy files into imports/ and import them (captures and legacy dashboard snapshots; idempotent)")
    p.add_argument("files", nargs="+")
    p = sub.add_parser("rebuild", help="Move the database aside and rebuild it from captures/ and imports/")
    p.add_argument("extra", nargs="*", help="Additional files to import (copied into imports/ first)")
    p.add_argument("--captures", default=argparse.SUPPRESS, help=argparse.SUPPRESS)  # Phase 1 spelling, after the command
    sub.add_parser("backfill", help="Re-scan sources indexed before Phase 2 (offsets, capture events, coverage)")
    sub.add_parser("check", help="Integrity of stored and derived data; exit status 1 on any problem")
    sub.add_parser("fingerprint", help="Content summary for comparing two databases")
    p = sub.add_parser("search"); p.add_argument("text", nargs="?", default="")
    p = sub.add_parser("show"); p.add_argument("identifier_type"); p.add_argument("identifier"); p.add_argument("--limit", type=int, default=20)
    sub.add_parser("status")
    args = parser.parse_args(argv)
    db = Path(args.db).expanduser()
    captures = Path(args.captures).expanduser() if args.captures else default_directory()
    if args.command == "rebuild":
        if db.exists():
            moved = db.with_name(f"{db.name}.bak-rebuild-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
            for suffix in ("", "-wal", "-shm"):
                if Path(str(db) + suffix).exists():
                    os.replace(str(db) + suffix, str(moved) + suffix)
            print(f"Previous database kept as {moved.name}")
        imports = Path(args.imports).expanduser() if args.imports else db.parent / "imports"
        args.files = [*sorted(str(p) for p in captures.glob("dronk-capture-*.jsonl")),
                      *sorted(str(p) for p in imports.glob("*") if p.is_file() and not p.name.startswith(".")), *args.extra]
        args.command = "import"
    analysis = {name: getattr(args, name) for name in ANALYSIS}
    store = Store(db, gap_seconds=analysis["sighting_gap_seconds"], packet_gap_seconds=analysis["packet_gap_seconds"],
                  edge_seconds=analysis["edge_seconds"], silence_seconds=analysis["status_silence_seconds"],
                  imports_dir=args.imports and Path(args.imports).expanduser(), capture_dir=captures)
    try:
        if args.command in ("import", "backfill"):
            if args.command == "import":
                results = []
                for start in range(0, len(args.files), IMPORT_MAX_FILES):
                    results += store.import_files(args.files[start:start + IMPORT_MAX_FILES])
            else:
                results = sync_captures(store, captures)
            total = Counter()
            for result in results:
                print(json.dumps(result))
                if "error" in result:
                    total["failed_files"] += 1
                total.update({k: v for k, v in result.items() if isinstance(v, int) and not isinstance(v, bool)})
            report = dict(total=dict(total), store=store.status())
            if args.command == "backfill":
                report["still_not_backfilled"] = store.check()["not_backfilled"]
            print(json.dumps(report))
            return 1 if total["failed_files"] else 0
        if args.command == "check":
            result = store.check()
            print(json.dumps(result, indent=1))
            return 0 if result["ok"] else 1
        if args.command == "fingerprint":
            print(json.dumps(store.fingerprint()))
        elif args.command == "search":
            print(json.dumps(store.search(args.text), indent=1))
        elif args.command == "show":
            print(json.dumps(store.aircraft_detail(args.identifier_type, args.identifier, limit=args.limit), indent=1))
        elif args.command == "status":
            print(json.dumps(store.status(), indent=1))
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    raise SystemExit(main())
