#!/usr/bin/env python3
"""DRONK Command Center: local SUSPECTRUM logs and manual host monitoring."""
import argparse
from collections import OrderedDict, deque
import csv
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import re
import secrets
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit
from configuration import resolve, validate_runtime, validate_credentials
import dronk_db
import dronk_history


ROOT = Path(__file__).resolve().parents[2]
STATIC = Path(__file__).with_name("static")
# Detection panel only: SUSPECTRUM reports status every 5 s, so 30 s is six missed records.
TELEMETRY_STALE_SECONDS = 30
ODID_ID_TYPES = dronk_db.ODID_ID_TYPES
DETECTION_LIMIT = 1024


def epoch(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


class State:
    def __init__(self, demo=False, mode=None):
        self.lock = threading.RLock()
        self.demo = demo
        self.operation_mode = mode or ("DEMO" if demo else "CAPTURE")
        self.alive = False
        self.host = dict(state="STOPPED", pid=None, error=None, reason=None)
        self.logs = deque(maxlen=1000)
        self.receivers = {}
        self.aircraft = {}
        self.history = deque(maxlen=1000)
        self.tak = dict(state="DISABLED", attempts=0, written=0, failed=0, acknowledged=False)
        self.stale_seconds = None
        self.last_detection = None
        # Valid decoded Remote ID that is not trackable (receiver reception records), by transmitter.
        self.receptions = {}
        # Aircraft detections by (identifier type, identifier): identity is kept separate from position, so
        # positionless Remote ID is recorded and exported. Kept across host sessions, like history.
        self.detections = {}
        # Receiver (session_id, seq) keys already counted, so a replayed or re-delivered event is not
        # counted twice. Older events without these fields cannot be deduplicated and are counted as before.
        self.seen = OrderedDict()
        self.capture = None
        self.db = dict(state="DISABLED", error=None, sync=None)

    def first_time(self, session_id, seq):
        if not session_id or seq is None:
            return True
        key = (session_id, str(seq))
        if key in self.seen:
            return False
        self.seen[key] = None
        if len(self.seen) > 8192:
            self.seen.popitem(last=False)
        return True

    def new_session(self):
        """A restarted host has a fresh tracker; earlier tracks must not count as active."""
        with self.lock:
            self.aircraft = {}
            self.receptions = {}
            self.last_detection = None
            self.capture = None

    def apply(self, event):
        with self.lock:
            kind = event.get("kind")
            self.alive = True
            if kind == "receiver":
                r = event["receiver"]
                if len(self.receivers) < 8 or r["stream"] in self.receivers:
                    self.receivers[r["stream"]] = r
            elif kind in ("observation", "reception"):
                aircraft = event["aircraft"]
                if bool(aircraft["synthetic"]) != self.demo:
                    raise ValueError("Synthetic/live isolation violated")
                entry = dict(aircraft, expires_at=event.get("expires_at"), tracking_reason=event.get("reason"), tracked=kind=="observation")
                if kind == "observation":
                    self.aircraft[aircraft["uid"]] = entry
                    rf = aircraft.get("rf") or {}
                    if self.first_time(rf.get("session_id"), rf.get("seq")):
                        self.detect((aircraft.get("identifier_type") or "UNKNOWN", aircraft["id"]), aircraft["received_at"], rf,
                                [], aircraft.get("position"), None, aircraft["uid"])
                    received = epoch(aircraft["received_at"])
                    self.last_detection = max(self.last_detection or received, received)
                self.history.appendleft(entry)
                if len(self.aircraft) > 1024:
                    del self.aircraft[next(iter(self.aircraft))]
            elif kind == "rid_reception":
                r = event["reception"]
                if bool(r["synthetic"]) != self.demo:
                    raise ValueError("Synthetic/live isolation violated")
                if not self.first_time(r.get("session_id"), r.get("seq")):
                    self.logs.appendleft(dict(time=datetime.now(timezone.utc).isoformat(), kind="duplicate", event=event))
                    return
                received = epoch(r["received_at"])
                # Same expiry as a track: the tracker's stale timeout after host receipt.
                self.receptions[r["transmitter"]] = dict(r, expires=received + (self.stale_seconds or 60))
                self.last_detection = max(self.last_detection or received, received)
                identity = self.reception_identity(r)
                if identity:
                    self.detect(identity, r["received_at"], r, r.get("messages") or [], None, r["reason"])
                if len(self.receptions) > 1024:
                    del self.receptions[next(iter(self.receptions))]
            elif kind == "tracks":
                valid = set(event["uids"])
                self.aircraft = {uid: a for uid, a in self.aircraft.items() if uid in valid}
            elif kind == "marker_state":
                # The bridge's own LIVE / LAST_KNOWN transition for the aircraft marker (operator/take-off are logged only).
                if bool(event.get("synthetic")) != self.demo:
                    raise ValueError("Synthetic/live isolation violated")
                record = self.detections.get((event.get("identifier_type"), event.get("id")))
                if record is not None and event.get("role") == "aircraft":
                    record["marker"] = {k: event.get(k) for k in ("state", "reason", "last_seen", "reported_at", "stale_at", "position")}
            elif kind == "tak":
                self.tak = {k: v for k, v in event.items() if k != "kind"}
            elif kind == "ready":
                self.stale_seconds = event.get("stale_seconds")
            elif kind == "capture":
                self.capture = {k: v for k, v in event.items() if k != "kind"}
                return  # periodic status: shown on the dashboard, not logged
            if kind != "tracks":
                self.logs.appendleft(dict(time=datetime.now(timezone.utc).isoformat(), kind=kind, event=event))

    reception_identity = staticmethod(dronk_db.reception_identity)

    def restore(self, records):
        """Aircraft from the SQLite store after a console restart: STALE until a new reception arrives."""
        with self.lock:
            for r in records:
                key = (r["identifier_type"], r["aircraft_id"])
                if key not in self.detections and bool(r["synthetic"]) == self.demo:
                    self.detections[key] = dict(r)

    def detect(self, key, received_at, rf, messages, position, reason, uid=None):
        """Create or update one aircraft detection; never invents coordinates. A valid position marks it
        position_valid (TRACKING) and is kept as last_position; a later positionless reception clears
        position_valid but keeps last_position with its own timestamp."""
        record = self.detections.get(key)
        if record is None:
            if len(self.detections) >= DETECTION_LIMIT:
                del self.detections[min(self.detections, key=lambda k: epoch(self.detections[k]["last_seen"]))]
            record = self.detections[key] = dict(aircraft_id=key[1], identifier_type=key[0], synthetic=self.demo,
                first_seen=received_at, last_seen=received_at, observation_count=0, receiver_ids=[], messages=[],
                rssi_min_dbm=None, rssi_max_dbm=None, last_position=None, last_position_at=None, uid=None)
        t = epoch(received_at)
        record["observation_count"] += 1
        record["restored"] = False
        if t < epoch(record["first_seen"]):
            record["first_seen"] = received_at
        valid = bool(position) and position.get("latitude") is not None and position.get("longitude") is not None
        if valid:
            record["uid"] = uid
            if record["last_position_at"] is None or t >= epoch(record["last_position_at"]):
                record.update(last_position=dict(position), last_position_at=received_at)
        receiver = rf.get("receiver_id") or rf.get("stream")
        if receiver:
            record["receiver_ids"] = sorted(set(record["receiver_ids"]) | {receiver})
        record["messages"] = sorted(set(record["messages"]) | set(messages))
        rssi = rf.get("rssi_dbm")
        if rssi is not None:
            record["rssi_min_dbm"] = rssi if record["rssi_min_dbm"] is None else min(record["rssi_min_dbm"], rssi)
            record["rssi_max_dbm"] = rssi if record["rssi_max_dbm"] is None else max(record["rssi_max_dbm"], rssi)
        if t >= epoch(record["last_seen"]) or record["observation_count"] == 1:
            record.update(last_seen=received_at, position_valid=valid, reason=reason, receiver_id=receiver,
                          transmitter=rf.get("transmitter"), transport=rf.get("transport"), channel=rf.get("channel"),
                          frequency_mhz=rf.get("frequency_mhz"), rssi_dbm=rssi)

    def detection_records(self, now):
        stale = self.stale_seconds or 60
        def state(r):
            marker = r.get("marker") or {}
            # LAST_KNOWN until a reception newer than the report arrives (then the bridge reports LIVE again).
            if marker.get("state") == "LAST_KNOWN" and marker.get("reported_at") and epoch(r["last_seen"]) <= epoch(marker["reported_at"]):
                return "LAST_KNOWN"
            if r.get("restored") or now - epoch(r["last_seen"]) >= stale:
                return "STALE"
            return "TRACKING" if r["position_valid"] else "DETECTED_NO_POSITION"
        records = [dict(r, state=state(r)) for r in self.detections.values()]
        return sorted(records, key=lambda r: epoch(r["last_seen"]), reverse=True)

    def snapshot(self):
        with self.lock:
            now = time.time()
            active = [a for a in self.aircraft.values() if epoch(a["expires_at"]) > now]
            connected = any(r.get("connected") and r.get("last_seen") and now - epoch(r["last_seen"]) < 90 for r in self.receivers.values())
            mode = "DEMO" if self.demo and self.alive else self.operation_mode if self.alive and connected else "DISCONNECTED"
            tak = dict(self.tak)
            if not self.alive and tak["state"] != "DISABLED":
                tak["state"] = "DISCONNECTED"
            return dict(name="DRONK Command Center", mode=mode, server_time=datetime.now(timezone.utc).isoformat(),
                        detection=self.detection(now, active), detections=self.detection_records(now),
                        receivers=[dict(r) for r in self.receivers.values()], aircraft=active, history=list(self.history), tak=tak,
                        history_limit=1000, operation_mode=self.operation_mode, host=dict(self.host), logs=list(self.logs), demo=self.demo,
                        capture=self.capture, db=dict(self.db))

    def detection(self, now, active):
        """Drone status from strictly decoded Remote ID only: tracked observations plus untrackable
        reception records (never RF, candidate or malformed counters).

        Tracks expire by the tracker's expires_at; receptions after the same stale timeout. A
        transmitter that is also tracked counts once."""
        fresh = [r for r in self.receivers.values() if r.get("connected") and r.get("last_seen")
                 and now - epoch(r["last_seen"]) < TELEMETRY_STALE_SECONDS]
        if self.host["state"] != "RUNNING" or not self.alive:
            state, reason = "OFFLINE", "Host monitoring " + self.host["state"].lower()
        elif not any(r.get("connected") for r in self.receivers.values()):
            state, reason = "OFFLINE", "Receiver disconnected"
        elif not fresh:
            state, reason = "OFFLINE", "Receiver telemetry stale"
        else:
            state, reason = ("DETECTED" if active else "CLEAR"), None
        tracked_transmitters = {(a.get("rf") or {}).get("transmitter") for a in active}
        untracked = sorted((r for r in self.receptions.values() if r["expires"] > now and r["transmitter"] not in tracked_transmitters),
                           key=lambda r: r["received_at"], reverse=True)
        if state == "CLEAR" and untracked:
            state = "DETECTED"
        last = self.last_detection
        return dict(state=state, reason=reason, active_count=len(active) + len(untracked), synthetic=self.demo,
                    tracked_count=len(active), untracked_count=len(untracked),
                    untracked=[dict(transmitter=r["transmitter"], reason=r["reason"], channel=r["channel"], transport=r["transport"],
                                    id=next((b["value"] for b in r["basic_ids"] if b["value"]), None),
                                    seconds_ago=max(0, int(now - epoch(r["received_at"])))) for r in untracked[:10]],
                    last_detection_at=datetime.fromtimestamp(last, timezone.utc).isoformat() if last else None,
                    seconds_since_last=max(0, int(now - last)) if last else None,
                    receivers_online=len(fresh), receivers_total=len(self.receivers), stale_seconds=self.stale_seconds)


DETECTION_COLUMNS = ["aircraft_id", "identifier_type", "state", "position_valid", "reason", "first_seen", "last_seen",
                     "observation_count", "receiver_id", "receiver_ids", "transport", "channel", "frequency_mhz", "rssi_dbm",
                     "rssi_min_dbm", "rssi_max_dbm", "messages", "transmitter", "synthetic",
                     "last_latitude", "last_longitude", "last_altitude_hae_m", "last_position_at"]


def csv_safe(values):
    return [("'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v) for v in values]


def export_detections_csv(detections):
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(DETECTION_COLUMNS)
    for d in detections:
        p = d.get("last_position") or {}
        row = dict(d, receiver_ids=" ".join(d["receiver_ids"]), messages=" ".join(d["messages"]),
                   last_latitude=p.get("latitude"), last_longitude=p.get("longitude"), last_altitude_hae_m=p.get("altitude_hae_m"))
        writer.writerow(csv_safe([row.get(c) for c in DETECTION_COLUMNS]))
    return out.getvalue().encode()


def export_csv(history):
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["id", "received_at", "observed_at", "latitude", "longitude", "altitude_hae_m", "source", "synthetic", "receiver_id", "rssi_dbm"])
    for a in history:
        p, rf = a.get("position") or {}, a.get("rf") or {}
        values = [a["id"], a["received_at"], a["observed_at"], p.get("latitude"), p.get("longitude"), p.get("altitude_hae_m"),
                  a["source"], a["synthetic"], rf.get("receiver_id"), rf.get("rssi_dbm")]
        writer.writerow(csv_safe(values))
    return out.getvalue().encode()


def make_server(state, port=8765, time_zone=dronk_history.DEFAULT_TIME_ZONE):
    streams = threading.BoundedSemaphore(8)
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *_):
            pass

        def allowed(self):
            expected = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            host = self.headers.get("Host", "")
            origin = self.headers.get("Origin")
            return host in expected and (not origin or origin == "http://" + host) and self.headers.get("Sec-Fetch-Site") != "cross-site"

        def response_headers(self, status, mime, length=None):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            if length is not None:
                self.send_header("Content-Length", str(length))
            self.end_headers()

        def reply(self, status, mime, body):
            self.response_headers(status, mime, len(body))
            self.wfile.write(body)

        def do_POST(self):
            if (not self.allowed() or self.headers.get("Origin") != "http://" + self.headers.get("Host", "")
                    or not secrets.compare_digest(self.headers.get("X-DRONK-CSRF", ""), token)):
                self.close_connection = True
                self.reply(403, "text/plain", b"Same-origin control token required")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 256 or self.headers.get("Content-Type") != "application/json":
                    raise ValueError("Invalid control request")
                body = json.loads(self.rfile.read(length))
                if self.path != "/api/control" or body not in ({"action": "start"}, {"action": "stop"}):
                    raise ValueError("Only host start/stop is supported")
                controller = getattr(self.server, "controller", None)
                if controller is None:
                    raise ValueError("Host configuration unavailable")
                getattr(controller, body["action"])()
                self.reply(200, "application/json", json.dumps(state.snapshot()["host"]).encode())
            except (ValueError, OSError, RuntimeError) as exc:
                self.close_connection = True
                self.reply(400, "text/plain", (str(exc) if isinstance(exc,ValueError) else "Host operation failed; private details withheld").encode())

        def do_GET(self):
            if not self.allowed():
                self.reply(403, "text/plain", b"Local same-origin access only")
                return
            url = urlsplit(self.path)
            if url.path == "/events":
                if not streams.acquire(blocking=False):
                    self.reply(503, "text/plain", b"Too many streams")
                    return
                self.response_headers(200, "text/event-stream")
                try:
                    while not self.server.stopping.is_set():
                        snapshot = state.snapshot()
                        snapshot["history"] = snapshot["history"][:150]
                        snapshot["detections"] = snapshot["detections"][:100]
                        body = json.dumps(snapshot, allow_nan=False, separators=(",", ":"))
                        self.wfile.write(("event: snapshot\ndata: " + body + "\n\n").encode())
                        self.wfile.flush()
                        self.server.stopping.wait(1)
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    pass
                finally:
                    streams.release()
                    self.close_connection = True
                return
            if url.path == "/api/control-token":
                self.reply(200, "application/json", json.dumps({"token": token}).encode())
                return
            if url.path == "/api/state":
                self.reply(200, "application/json", json.dumps(state.snapshot(), allow_nan=False).encode())
                return
            if url.path in ("/api/aircraft", "/api/aircraft/detail"):
                store = getattr(self.server, "store", None)
                if store is None:
                    self.reply(503, "text/plain", b"Aircraft history unavailable (no database)")
                    return
                query = parse_qs(url.query)
                one = lambda name, default=None: query.get(name, [default])[0]
                try:
                    with self.server.store_lock:
                        if url.path == "/api/aircraft":
                            body = store.search(one("q", "")[:64], int(one("limit", "50")))
                        else:
                            before = one("before")
                            body = store.aircraft_detail(one("type", ""), one("id", ""), int(before) if before else None, int(one("limit", "100")))
                except ValueError:
                    self.reply(400, "text/plain", b"Invalid query")
                    return
                if body is None:
                    self.reply(404, "text/plain", b"Aircraft not found")
                else:
                    self.reply(200, "application/json", json.dumps(body, allow_nan=False).encode())
                return
            if url.path in HISTORY_ROUTES:
                store = getattr(self.server, "store", None)
                if store is None:
                    self.reply(503, "text/plain", b"Aircraft history unavailable (no database)")
                    return
                query = parse_qs(url.query)
                one = lambda name, default=None: query.get(name, [default])[0]
                number = lambda name: int(one(name)) if one(name) not in (None, "") else None
                try:
                    tz = dronk_history.zone(one("tz") or self.server.time_zone)
                    with self.server.store_lock:
                        body = HISTORY_ROUTES[url.path](store, one, number, tz)
                except (ValueError, dronk_db.StoreError) as exc:
                    self.reply(400, "text/plain", (str(exc) if isinstance(exc, dronk_db.StoreError) else "Invalid query").encode())
                    return
                if body is None:
                    self.reply(404, "text/plain", b"Not found")
                else:
                    self.reply(200, "application/json", json.dumps(body, allow_nan=False).encode())
                return
            if url.path == "/api/export":
                query = parse_qs(url.query)
                fmt, table = query.get("format", ["json"])[0], query.get("table", ["detections"])[0]
                if fmt not in ("json", "csv") or table not in ("detections", "history"):
                    self.reply(400, "text/plain", b"Use format json or csv, table detections or history")
                else:
                    rows = state.snapshot()[table]
                    body = (export_csv if table == "history" else export_detections_csv)(rows) if fmt == "csv" \
                        else json.dumps(rows, allow_nan=False).encode()
                    self.reply(200, "text/csv" if fmt == "csv" else "application/json", body)
                return
            path = STATIC / ("index.html" if url.path == "/" else url.path.lstrip("/"))
            if not path.resolve().is_relative_to(STATIC.resolve()) or not path.is_file():
                self.reply(404, "text/plain", b"Not found")
                return
            mime = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css"}.get(path.suffix, "text/plain")
            self.reply(200, mime, path.read_bytes())

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.stopping = threading.Event()
    server.store, server.store_lock = None, threading.Lock()
    server.time_zone = time_zone
    return server


def _when(value):
    """Query time: epoch milliseconds or an ISO 8601 timestamp with offset."""
    if value is None or value == "":
        raise ValueError("time required")
    return int(value) if value.lstrip("-").isdigit() else dronk_db.epoch_ms(value)


def _aircraft_route(function, *extra):
    return lambda store, one, number, tz: function(store, one("type", ""), one("id", ""), tz, *(f(one, number) for f in extra))


# Historical queries (Phase 2.3). Measured and derived values are kept apart in every response (dronk_history).
HISTORY_ROUTES = {
    "/api/aircraft/summary": _aircraft_route(dronk_history.summary),
    "/api/aircraft/sightings": _aircraft_route(dronk_history.sightings, lambda one, n: n("before"), lambda one, n: n("limit")),
    "/api/aircraft/appearances": _aircraft_route(dronk_history.appearances, lambda one, n: one("period", "day")),
    "/api/aircraft/receptions": _aircraft_route(dronk_history.receptions, lambda one, n: n("sighting"), lambda one, n: n("before"),
                                                lambda one, n: n("limit")),
    "/api/aircraft/track": _aircraft_route(dronk_history.track, lambda one, n: n("sighting")),
    "/api/aircraft/markers": _aircraft_route(dronk_history.markers, lambda one, n: n("limit")),
    "/api/provenance": lambda store, one, number, tz: dronk_history.provenance(store, int(one("reception", "")), tz),
    "/api/coverage": lambda store, one, number, tz: dronk_history.coverage(store, _when(one("from")), _when(one("to")), tz),
}


class Database:
    """Console side of the SQLite store: restore on start, history API, and catch-up imports of captures
    the live index didn't fully cover (each on its own connection; the capture files stay authoritative)."""
    def __init__(self, state, server, config):
        self.state, self.server, self.config = state, server, config
        self.sync_lock = threading.Lock()

    def open(self):
        path = self.config.get("db_path")
        if not path:
            return
        try:
            store = self.store()
            self.state.restore(store.restore_detections())
            self.server.store = store
            self.set(state="READY", error=None)
        except (dronk_db.StoreError, sqlite3.Error, OSError) as error:
            self.set(state="UNAVAILABLE", error=str(error) if isinstance(error, dronk_db.StoreError) else type(error).__name__)
            return
        self.sync()

    def store(self):
        """Configured analysis thresholds apply to the database (and so to the live indexer, which follows it)."""
        a = self.config.get("analysis") or {}
        return dronk_db.Store(self.config["db_path"], synthetic=self.state.demo, gap_seconds=a.get("sighting_gap_seconds"),
                              packet_gap_seconds=a.get("packet_gap_seconds"), edge_seconds=a.get("edge_seconds"),
                              silence_seconds=a.get("status_silence_seconds"), capture_dir=self.config.get("capture_dir"))

    def set(self, **fields):
        with self.state.lock:
            self.state.db.update(fields)

    def sync(self):
        """Background, at console start and after every host session."""
        directory = self.config.get("capture_dir")
        if not directory or self.server.store is None:
            return
        def run():
            if not self.sync_lock.acquire(blocking=False):
                return
            try:
                store = self.store()
                try:
                    # A capture still being written has no capture_end and a fresh mtime; sync_captures skips it.
                    results = dronk_db.sync_captures(store, directory)
                    with self.server.store_lock:
                        self.state.restore(store.restore_detections())
                finally:
                    store.close()
                self.set(sync=dict(at=datetime.now(timezone.utc).isoformat(), files=len(results),
                                   accepted=sum(r.get("accepted", 0) for r in results), errors=sum(1 for r in results if "error" in r)))
            except (dronk_db.StoreError, sqlite3.Error, OSError) as error:
                self.set(sync=dict(at=datetime.now(timezone.utc).isoformat(), error=type(error).__name__))
            finally:
                self.sync_lock.release()
        threading.Thread(target=run, name="dronk-sync", daemon=True).start()


class HostController:
    """Persistent local supervisor; each start owns a fresh monitoring process group."""
    def __init__(self, state, config):
        self.state, self.config = state, config
        self.lock = threading.RLock()
        self.child = None
        self.after_session = None

    def start(self):
        with self.lock:
            self.config = validate_runtime(self.config)
            validate_credentials(self.config)
            if self.child and self.child.poll() is None:
                return
            if not self.config.get("demo") and not self.config.get("devices"):
                raise ValueError("Configure --device paths before starting host monitoring")
            self.state.new_session()
            child = subprocess.Popen([sys.executable, str(Path(__file__).with_name("host_worker.py"))],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, start_new_session=True)
            self.child = child
            with self.state.lock:
                self.state.host = dict(state="STARTING", pid=child.pid, error=None, reason=None)
            child.stdin.write((json.dumps(self.config) + "\n").encode()); child.stdin.close()
            threading.Thread(target=self.consume, args=(child,), daemon=True).start()

    def consume(self, child):
        error, complete = None, False
        try:
            for line in iter(lambda: child.stdout.readline(65537), b""):
                if len(line) > 65536:
                    raise ValueError("Oversized host event")
                event = json.loads(line)
                with self.state.lock:
                    if child is not self.child:
                        return
                    if self.state.host["state"] == "STOPPING" and event.get("kind") != "capture":
                        continue  # the final capture status (CLOSED) still reaches the dashboard
                    if event.get("kind") == "error":
                        error = event.get("code", "E_BRIDGE_INTERNAL") + ": " + event.get("message", "Check host configuration")
                    if event.get("kind") == "ready":
                        self.state.host["state"] = "RUNNING"
                    if event.get("kind") == "complete":
                        complete = True
                    self.state.apply(event)
        except (ValueError, KeyError) as exc:
            error = str(exc)
            child.terminate()
        finally:
            code = child.wait()
            # Terminate owned descendants if the worker failed before its finally block.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.stdout.close()
            with self.state.lock:
                if child is self.child and self.state.host["state"] != "STOPPING":
                    if complete and code == 0 and not error:
                        self.disconnected("STOPPED", reason="Timed capture complete")
                    else:
                        how = f"signal {-code}" if code < 0 else f"code {code}"
                        self.disconnected("ERROR", error or f"Host process exited unexpectedly ({how})")
            if self.after_session:
                self.after_session()  # the worker has exited, so its capture is closed (or interrupted)

    def disconnected(self, status, error=None, reason=None):
        self.state.alive = False
        self.state.host = dict(state=status, pid=None, error=error, reason=reason)
        for receiver in self.state.receivers.values():
            receiver["connected"] = False
            receiver["connection_state"] = "not_observed"
        if self.state.tak["state"] != "DISABLED":
            self.state.tak["state"] = "DISCONNECTED"
        self.state.logs.appendleft(dict(time=datetime.now(timezone.utc).isoformat(), kind="host", event={"state": status, "error": error, "reason": reason}))

    def stop(self):
        with self.lock:
            child = self.child
            was_running = child is not None and child.poll() is None
            with self.state.lock:
                self.state.host["state"] = "STOPPING"
            status, error = "STOPPED", None
            try:
                if was_running:
                    child.terminate()
                    try:
                        child.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait()
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            except OSError:
                status, error = "ERROR", "Host process could not be stopped cleanly"
                raise
            finally:
                # Never leave STOPPING behind. Clear the child under the state lock so
                # the consumer cannot report this requested exit as an unexpected one.
                # After a failed stop keep it, so Start cannot spawn a second worker
                # on the same TTYs and Stop can be retried.
                with self.state.lock:
                    self.disconnected(status, error)
                    if status == "STOPPED":
                        self.child = None


def pairs(values):
    result = {}
    for value in values:
        key, separator, val = value.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", key) or key in result or not val:
            raise ValueError("Use unique alias=value pairs")
        result[key] = val
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--device", action="append", default=[], help="Explicit alias=/dev/serial/by-id/...; repeat for multiple receivers")
    parser.add_argument("--expect-id", action="append", default=[], help="alias=expected-receiver-id")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--mode", help="DEMO, CAPTURE or LIVE")
    parser.add_argument("--config", help="Private TOML path; defaults to DRONK_CONFIG or user config directory")
    parser.add_argument("--receiver-alias", action="append", default=[])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--duration", type=float, default=0,
                        help="Timed diagnostic capture: each started session stops after this many seconds. "
                             "Default 0 monitors until Stop DRONK; the console runs until Ctrl-C either way")
    parser.add_argument("--processor", default=str(ROOT / "linux-bridge/build/install/linux-bridge/bin/linux-bridge"))
    parser.add_argument("--capture-dir", help="Append-only JSONL capture directory (default ~/.local/state/dronk/captures)")
    parser.add_argument("--no-capture", action="store_true", help="Do not write a capture file for started sessions")
    parser.add_argument("--db", help="SQLite store (default ~/.local/state/dronk/dronk.sqlite3)")
    parser.add_argument("--no-db", action="store_true", help="No SQLite store: no live index, no restore, no history search")
    parser.add_argument("--time-zone", help="IANA display time zone for history (default America/Chicago; storage is always UTC)")
    parser.add_argument("--forward-live", action="store_true")
    parser.add_argument("--forward-pilot-location", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--tak-host")
    parser.add_argument("--tak-connect-address")
    parser.add_argument("--tak-port")
    for name in ("client-p12", "password-file", "ca-file"):
        parser.add_argument("--" + name)
    args, unknown = parser.parse_known_args()
    if unknown: parser.error("Unsupported DRONK option; values withheld")
    try:
        devices, expected = pairs(args.device), pairs(args.expect_id)
        config = resolve(args, devices, expected)
        if args.receiver_alias:
            config['receiver_aliases'].update(pairs(args.receiver_alias))
            config = validate_runtime(config)
        if not 0 <= args.duration <= 86400:
            raise ValueError("Duration must be 0–86400 seconds")
    except ValueError as exc:
        parser.error(str(exc))
    state = State(config["demo"], config["mode"])
    server = make_server(state, args.port, config["analysis"]["time_zone"])
    running = threading.Event(); running.set()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: running.clear())
    config["duration"] = args.duration
    controller = HostController(state, config)
    server.controller = controller
    database = Database(state, server, config)
    controller.after_session = database.sync
    database.open()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    limit = f"timed capture {args.duration:g} s per session" if args.duration else "monitoring until Stop DRONK"
    print(f"DRONK Command Center: http://127.0.0.1:{server.server_port} (host STOPPED; {limit})", flush=True)
    try:
        while running.is_set():
            time.sleep(0.1)
    finally:
        controller.stop()
        server.stopping.set(); server.shutdown(); server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
