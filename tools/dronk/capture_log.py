"""Append-only JSONL capture of one host monitoring session (DRONK capture format, see CAPTURE.md).

Raw receiver lines are written as received, before any validation, tracking or synthetic filter; the
bridge's validated events follow as separate records. A capture file is never reopened or overwritten:
each session creates a new uniquely named file with O_EXCL. Nothing here deletes captures."""
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import struct
import threading
import time
import uuid

from serial_sources import DATA, CONNECTED, DISCONNECTED, ERROR

SCHEMA, VERSION = "dronk.capture", 1
MAX_LINE = 4096  # above the hub's 3070-byte line limit, so every line the hub accepts is captured whole
MIN_FREE_BYTES = 512 * 2**20
MAX_FILE_BYTES = 2 * 2**30
DISK_CHECK_BYTES = 2**20
DISK_CHECK_SECONDS = 10
SYNC_SECONDS = 2
# Session-relative counters reported in diagnostics; the first status after connect is the baseline.
WATCHED = ("usb_output_drops", "malformed_frames", "queue_drops", "oversize_drops", "output_errors", "errors",
           "rf_frames", "candidate_frames", "decoded_packs", "rid_receptions")
STALE_SLACK_SECONDS = 10
PROBLEMS = ("usb_output_drops", "malformed_frames", "queue_drops", "oversize_drops", "output_errors")
# Validated Remote ID content vs. receiver/host telemetry. Raw receiver lines are layer "raw_receiver".
DECODED = {"rid_reception", "observation", "reception"}
MARKERS = {"marker_state"}  # the bridge's LIVE / LAST_KNOWN marker transitions, indexed like decoded records
SKIPPED = {"tracks"}  # periodic active-UID lists; derivable from the observations


class CaptureError(Exception):
    """Capture cannot start; monitoring must not run without it."""


def utc(ms=None):
    t = time.time() if ms is None else ms / 1000
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def private_directory(path):
    """Create path and any missing parents as 0700 (mkdir(parents=True) gives parents the umask default,
    which left ~/.local/state/dronk world-readable). Existing directories are not changed."""
    path = Path(path)
    missing = []
    while not path.exists():
        missing.append(path)
        path = path.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        os.chmod(directory, 0o700)  # mode is filtered by the umask; set it exactly


def default_directory(environ=None):
    env = os.environ if environ is None else environ
    return Path(env.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "dronk" / "captures"


class CaptureLog:
    def __init__(self, directory, mode, streams, synthetic=False, min_free_bytes=MIN_FREE_BYTES,
                 max_file_bytes=MAX_FILE_BYTES, disk_usage=shutil.disk_usage, clock=time.monotonic):
        self.lock = threading.Lock()
        self.directory, self.streams = Path(directory), list(streams)
        self.min_free, self.max_bytes, self.disk_usage, self.clock = min_free_bytes, max_file_bytes, disk_usage, clock
        self.capture_id = str(uuid.uuid4())
        self.mode = mode
        self.records = self.bytes = self.since_check = 0
        self.suspended = None
        self.buffers = {i: bytearray() for i in range(len(self.streams))}
        self.discarding = {i: False for i in range(len(self.streams))}
        self.receivers = {}  # stream -> {session_id, baseline, lifetime, delta, disconnects, ...}
        self.usb_disconnects = {s: 0 for s in self.streams}
        self.last_tak = None
        self.last_receiver = {}  # stream -> previous telemetry without last_seen
        self.closing = False  # set before the host closes its own TTYs: those closes are not disconnects
        try:
            private_directory(self.directory)
            free = self.disk_usage(self.directory).free
        except OSError:
            raise CaptureError("Capture directory unavailable") from None
        if free < self.min_free:
            raise CaptureError("Not enough free disk space for a capture")
        previous = sorted(p.name for p in self.directory.glob("dronk-*.jsonl"))
        now = datetime.now(timezone.utc)
        stamp = now.strftime("%Y%m%dT%H%M%S") + f".{now.microsecond // 1000:03d}Z"  # sorts in start order
        prefix = "dronk-demo-" if synthetic else "dronk-capture-"
        self.path = self.directory / f"{prefix}{stamp}-{self.capture_id[:8]}.jsonl"
        try:
            self.fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND | os.O_CLOEXEC, 0o600)
        except OSError:
            raise CaptureError("Cannot create a new capture file") from None
        self.last_sync = self.last_check = self.clock()
        self.started_at = utc()
        self.write(t=self.started_at, record=dict(type="capture_start", schema=SCHEMA, version=VERSION, mode=mode, synthetic=synthetic,
                        streams=self.streams, previous_capture=previous[-1] if previous else None,
                        limits=dict(max_line=MAX_LINE, min_free_bytes=self.min_free, max_file_bytes=self.max_bytes)))
        self.sync()

    # -- writing ---------------------------------------------------------------------------------
    def write(self, record, t=None, final=False):
        """One os.write per record, so an interruption can only truncate the final line. Returns (n, t, offset)."""
        if self.fd is None or (self.suspended and not final):
            return None
        record = dict(capture_id=self.capture_id, n=self.records, t=t or utc(), **record)
        line = (json.dumps(record, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n").encode()
        if self.bytes + len(line) > self.max_bytes and not final:
            return self.suspend("max_file_size")
        os.write(self.fd, line)
        position = (self.records, record["t"], self.bytes)  # record number, time, byte offset in the file
        self.records += 1
        self.bytes += len(line)
        self.since_check += len(line)
        if self.since_check >= DISK_CHECK_BYTES:
            self.check_disk()
        return position

    def suspend(self, reason):
        """Stop writing for the rest of the session and say so in the file; never delete anything."""
        marker = dict(capture_id=self.capture_id, n=self.records, t=utc(), type="capture_suspended", reason=reason)
        data = (json.dumps(marker, separators=(",", ":")) + "\n").encode()
        try:
            os.write(self.fd, data)
            os.fsync(self.fd)
            self.bytes += len(data)
        except OSError:
            pass
        self.records += 1
        self.suspended = reason

    def check_disk(self):
        self.since_check, self.last_check = 0, self.clock()
        try:
            low = self.disk_usage(self.directory).free < self.min_free
        except OSError:
            low = True
        if low:
            self.suspend("low_disk_space")

    def sync(self):
        if self.fd is not None:
            try:
                os.fsync(self.fd)
            except OSError:
                pass
            self.last_sync = self.clock()

    def tick(self):
        """Periodic from the worker loop: bounded data loss on power failure, periodic disk check."""
        with self.lock:
            now = self.clock()
            if now - self.last_check >= DISK_CHECK_SECONDS and not self.suspended:
                self.check_disk()
            if now - self.last_sync >= SYNC_SECONDS:
                self.sync()

    # -- raw receiver side (worker main thread) --------------------------------------------------
    def envelope(self, data):
        """Tap of one multiplex envelope sent to the bridge (serial_sources.envelope layout)."""
        kind, index, ms, length = struct.unpack(">BHqI", data[:15])
        if index >= len(self.streams):
            return
        stream = self.streams[index]
        with self.lock:
            if kind == DATA:
                self.raw(index, data[15:15 + length], ms)
            elif kind in (CONNECTED, DISCONNECTED, ERROR):
                self.flush_partial(index, ms)
                event = {CONNECTED: "connected", DISCONNECTED: "disconnected", ERROR: "error"}[kind]
                if kind == DISCONNECTED and self.closing:
                    event = "closed_by_host"  # requested stop, not a USB disconnect
                elif kind != CONNECTED:
                    self.usb_disconnects[stream] += 1
                self.write(dict(type="usb", stream=stream, event=event, disconnects=self.usb_disconnects[stream]), utc(ms))
                if kind != CONNECTED:
                    self.sync()

    def raw(self, index, chunk, ms):
        buffer = self.buffers[index]
        start = 0
        while True:
            end = chunk.find(b"\n", start)
            piece = chunk[start:] if end < 0 else chunk[start:end]
            if self.discarding[index]:
                if end >= 0:
                    self.discarding[index] = False
            else:
                room = MAX_LINE - len(buffer)
                buffer += piece[:room]
                if len(piece) > room:
                    self.line(index, bytes(buffer), ms, truncated=True)
                    buffer.clear()
                    self.discarding[index] = end < 0
                elif end >= 0:
                    self.line(index, bytes(buffer), ms)
                    buffer.clear()
            if end < 0:
                return
            start = end + 1

    def flush_partial(self, index, ms):
        if self.buffers[index]:
            self.line(index, bytes(self.buffers[index]), ms, partial=True)
            self.buffers[index].clear()
        self.discarding[index] = False

    def line(self, index, data, ms, truncated=False, partial=False):
        data = data.rstrip(b"\r")
        record = dict(type="rx_line", layer="raw_receiver", stream=self.streams[index])
        try:
            record["line"] = data.decode("utf-8")
        except UnicodeDecodeError:
            record["line_b64"] = base64.b64encode(data).decode()
        if truncated:
            record["truncated"] = True
        if partial:
            record["partial"] = True
        self.write(record, utc(ms))

    # -- bridge side (worker consumer thread) ----------------------------------------------------
    def note(self, record):
        """A host-side record (e.g. db_error) from another thread."""
        with self.lock:
            self.write(record)

    def bridge(self, event):
        """Returns (n, t, offset) of a written decoded Remote ID or marker record, for the live index; otherwise None."""
        kind = event.get("kind")
        if kind in SKIPPED:
            return None
        with self.lock:
            if kind == "tak":
                if event == self.last_tak:
                    return None  # repeated unchanged uplink state
                self.last_tak = event
            if kind == "receiver":
                r = event["receiver"]
                same = {k: v for k, v in r.items() if k != "last_seen"}
                if self.last_receiver.get(r.get("stream")) == same:
                    return None  # unchanged telemetry repeated by the bridge; raw status lines are still captured
                self.last_receiver[r.get("stream")] = same
            layer = "decoded_remote_id" if kind in DECODED else "marker" if kind in MARKERS else "telemetry"
            position = self.write(dict(type="bridge", layer=layer, event=event))
            if kind == "receiver":
                self.diagnose(event["receiver"])
            return position if layer in ("decoded_remote_id", "marker") else None

    def diagnose(self, r):
        """Counter changes since this host session first saw the receiver's current boot session."""
        stream, counters, session = r.get("stream"), r.get("counters") or {}, r.get("session_id")
        if not session or not counters:
            return
        state = self.receivers.get(stream)
        uptime, now = r.get("uptime_us"), self.clock()
        stale = (state is not None and state["session_id"] == session and isinstance(uptime, int)
                 and isinstance(state.get("uptime_us"), int)
                 and (uptime - state["uptime_us"]) / 1e6 > now - state["seen"] + STALE_SLACK_SECONDS)
        if state is None or state["session_id"] != session or stale:
            if state is not None and not stale:
                self.write(dict(type="receiver_session", stream=stream, session_id=session,
                                previous_session_id=state["session_id"], reset_reason=r.get("reset_reason")))
            # A stale baseline is a status the receiver buffered while no host was reading (it is
            # delivered first on connect): its uptime then jumps far beyond the host time that passed.
            state = self.receivers[stream] = dict(session_id=session, baseline={k: counters.get(k) for k in WATCHED},
                                                  baseline_at=utc(), uptime_us=uptime, delta={},
                                                  rebaselined="stale_buffered_status" if stale else None)
        delta = {k: counters[k] - state["baseline"][k] for k in WATCHED
                 if isinstance(counters.get(k), int) and isinstance(state["baseline"].get(k), int)}
        state.update(lifetime={k: counters.get(k) for k in WATCHED}, uptime_us=uptime, seen=now, connected=r.get("connected"))
        if delta != state["delta"]:
            changed = {k: v for k, v in delta.items() if v != state["delta"].get(k)}
            state["delta"] = delta
            # The first delta and every problem-counter change get a record; traffic counters ride along.
            if not state.get("logged") or any(k in changed for k in PROBLEMS):
                state["logged"] = True
                self.write(dict(type="diagnostics", stream=stream, session_id=session, baseline_at=state["baseline_at"],
                                rebaselined=state["rebaselined"], delta=delta, lifetime=state["lifetime"], usb_disconnects=self.usb_disconnects.get(stream, 0)))

    # -- status and close ------------------------------------------------------------------------
    def status(self):
        with self.lock:
            return dict(kind="capture", file=self.path.name, capture_id=self.capture_id, records=self.records,
                        bytes=self.bytes, state="SUSPENDED" if self.suspended else "RECORDING" if self.fd is not None else "CLOSED",
                        suspended_reason=self.suspended,
                        receivers=[dict(stream=s, session_id=(self.receivers.get(s) or {}).get("session_id"),
                                        delta=(self.receivers.get(s) or {}).get("delta", {}),
                                        usb_disconnects=self.usb_disconnects[s]) for s in self.streams])

    def close(self, reason):
        with self.lock:
            if self.fd is None:
                return
            for index in self.buffers:
                self.flush_partial(index, None)
            try:  # the end marker is attempted even when suspended, so the session boundary is explicit
                self.write(dict(type="capture_end", reason=reason, records=self.records, suspended=self.suspended,
                                usb_disconnects=self.usb_disconnects,
                                receivers={s: dict(session_id=v["session_id"], delta=v["delta"]) for s, v in self.receivers.items()}),
                           final=True)
            except OSError:
                pass
            self.sync()
            os.close(self.fd)
            self.fd = None


def read_capture(path):
    """Tolerant reader for import and recovery: keeps every complete record, reports what it skipped.

    A missing capture_end means the session was interrupted (crash, kill, power loss); a final line
    without a newline is an interrupted write and is never parsed."""
    data = Path(path).read_bytes()
    lines = data.split(b"\n")
    tail = lines.pop()  # b"" when the file ends with a newline
    records, malformed = [], 0
    for raw in lines:
        try:
            record = json.loads(raw)
            if not isinstance(record, dict) or "type" not in record:
                raise ValueError
            records.append(record)
        except ValueError:
            malformed += 1
    header = records[0] if records and records[0].get("type") == "capture_start" else None
    ids = {r.get("capture_id") for r in records}
    return dict(records=records, header=header, complete=bool(records) and records[-1].get("type") == "capture_end",
                truncated_tail=bool(tail), malformed_lines=malformed, consistent=header is not None and ids == {header["capture_id"]})
