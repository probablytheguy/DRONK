"""DRONK observation quality (Phase 2, quality-v1): receiver coverage, known losses and sighting quality.

Everything here is DERIVED from measured rows (capture_events, receiver_lines, receptions) and recomputable
from the database alone, so changing a threshold never needs the capture files. Coverage means a receiver
was connected and producing protocol lines; it is not RF coverage of a channel (the receiver hops)."""
import bisect
import json
import statistics

QUALITY = "quality-v1"
PACKET_GAP_SECONDS = 10      # quality warning only; sightings still split at the 300 s sighting gap
EDGE_SECONDS = 30
SILENCE_SECONDS = 15         # three receiver status periods
BUFFER_SLACK_SECONDS = 2     # uptime running ahead of host time by more: the earlier status was buffered on the receiver
CERTAIN = ("usb_output_drops", "queue_drops", "oversize_drops")   # receiver dropped output or frames
POSSIBLE = ("malformed_frames",)                                  # undecodable frames; may or may not have been Remote ID
LOSS_COUNTERS = CERTAIN + POSSIBLE
LIVE_LINES = ("empty", "unparsed", "fragment")  # not evidence that the receiver is running


def derive_source(db, source_id, silence_seconds=SILENCE_SECONDS):
    """Replace one source's coverage and known losses. Returns (start_ms, end_ms) touched, or None."""
    db.execute("DELETE FROM coverage WHERE source_id = ?", (source_id,))
    db.execute("DELETE FROM known_losses WHERE source_id = ?", (source_id,))
    source = db.execute("SELECT kind, complete, truncated_tail FROM sources WHERE id = ?", (source_id,)).fetchone()
    if source is None or source["kind"] != "capture":
        return None  # legacy snapshots carry no capture evidence: quality stays unknown
    events = db.execute("SELECT record_n, t_ms, type, stream, event, session_id FROM capture_events WHERE source_id = ?", (source_id,)).fetchall()
    lines = db.execute("SELECT record_n, t_ms, stream, line_type, session_id, uptime_us, counters FROM receiver_lines "
                       "WHERE source_id = ?", (source_id,)).fetchall()
    timeline = sorted([("event", r) for r in events] + [("line", r) for r in lines], key=lambda item: item[1]["record_n"])
    if not timeline:
        return None
    silence_ms = silence_seconds * 1000
    open_ = {}       # stream -> current interval
    after = {}       # stream -> start reason for the next interval
    covered, losses = [], []

    def close(stream, reason):
        interval = open_.pop(stream, None)
        if interval:
            covered.append((stream, interval["session"], interval["start"], interval["last"], interval["why"], reason, interval["lines"]))
        after[stream] = {"status_silence": "resumed", "receiver_reboot": "receiver_reboot"}.get(reason, "first_line")

    def loss(stream, session, start, end, kind, amount, certain, evidence):
        losses.append((stream, session, start, end, kind, amount, int(certain), evidence))

    last_t, finished = timeline[0][1]["t_ms"], False
    for kind, r in timeline:
        last_t = max(last_t, r["t_ms"])
        if kind == "event":
            if r["type"] == "usb" and r["stream"]:
                if r["event"] == "connected":
                    close(r["stream"], "reconnected")
                    after[r["stream"]] = "connected"
                else:
                    close(r["stream"], r["event"])
            elif r["type"] == "capture_suspended":
                for stream in list(open_):
                    close(stream, "capture_suspended")
                loss(None, None, r["t_ms"], r["t_ms"], "capture_suspended", None, True, "capture_marker")
                finished = "suspended"
            elif r["type"] == "capture_end":
                for stream in list(open_):
                    close(stream, "capture_end")
                finished = finished or "ended"
            continue
        if finished or r["line_type"] in LIVE_LINES:
            continue
        stream, t, current = r["stream"], r["t_ms"], open_.get(r["stream"])
        if current and t - current["last"] > silence_ms:
            close(stream, "status_silence"); current = None
        if current and r["session_id"] and current["session"] and r["session_id"] != current["session"]:
            close(stream, "receiver_reboot"); current = None
        if current is None:
            current = open_[stream] = dict(start=t, last=t, session=r["session_id"], why=after.get(stream, "first_line"), lines=0, status=None)
        current.update(last=t, lines=current["lines"] + 1, session=current["session"] or r["session_id"])
        if r["line_type"] == "status" and r["counters"] and isinstance(r["uptime_us"], int):
            counters, previous = json.loads(r["counters"]), current["status"]
            if previous and previous["session"] == r["session_id"]:
                ahead = (r["uptime_us"] - previous["uptime"]) / 1e6 - (t - previous["t"]) / 1000
                if ahead <= BUFFER_SLACK_SECONDS:  # otherwise the earlier status sat in the receiver's buffer: no window to blame
                    for name in LOSS_COUNTERS:
                        a, b = previous["counters"].get(name), counters.get(name)
                        if isinstance(a, int) and isinstance(b, int) and b > a:
                            loss(stream, r["session_id"], previous["t"], t, name, b - a, name in CERTAIN, "receiver_counters")
            current["status"] = dict(t=t, uptime=r["uptime_us"], counters=counters, session=r["session_id"])
    for stream in list(open_):
        close(stream, "interrupted")
    if not source["complete"] and finished != "suspended":
        loss(None, None, last_t, last_t, "interrupted_capture", None, True, "file_state")
    if source["truncated_tail"]:
        loss(None, None, last_t, last_t, "truncated_record", 1, True, "file_state")
    db.executemany("INSERT INTO coverage (source_id, stream, session_id, start_ms, end_ms, start_reason, end_reason, line_count, "
                   "silence_seconds, derivation) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   [(source_id, *c, silence_seconds, QUALITY) for c in covered])
    db.executemany("INSERT INTO known_losses (source_id, stream, session_id, start_ms, end_ms, kind, amount, certain, evidence, derivation) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [(source_id, *l, QUALITY) for l in losses])
    return min(r["t_ms"] for _, r in timeline), last_t


def union(db, start_ms, end_ms):
    """Coverage of any receiver overlapping [start_ms, end_ms], merged into disjoint intervals."""
    rows = db.execute("SELECT start_ms, end_ms FROM coverage WHERE end_ms >= ? AND start_ms <= ? ORDER BY start_ms",
                      (start_ms, end_ms)).fetchall()
    merged = []
    for s, e in rows:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged


def sighting_quality(db, sighting, times, packet_gap_seconds=PACKET_GAP_SECONDS, edge_seconds=EDGE_SECONDS,
                     silence_seconds=SILENCE_SECONDS):
    """Quality fields for one sighting from its sorted reception times (ms)."""
    gaps = [b - a for a, b in zip(times, times[1:])]
    start, end, edge = sighting["start_ms"], sighting["end_ms"], edge_seconds * 1000
    fields = dict(max_gap_ms=max(gaps) if gaps else None, median_gap_ms=int(statistics.median(gaps)) if gaps else None,
                  long_gaps=sum(g > packet_gap_seconds * 1000 for g in gaps), packet_gap_seconds=packet_gap_seconds,
                  edge_seconds=edge_seconds, silence_seconds=silence_seconds, quality_derivation=QUALITY,
                  coverage="unknown", open_start=None, open_end=None, coverage_gaps=None, disconnects=None, losses=None, possible_losses=None)
    intervals = union(db, start, end)
    starts = [s for s, _ in intervals]
    def containing(t):
        i = bisect.bisect_right(starts, t) - 1
        return intervals[i] if i >= 0 and intervals[i][1] >= t else None
    inside = sum(1 for t in times if containing(t))
    if not inside:
        return fields
    first, last = containing(times[0]), containing(times[-1])
    fields.update(
        coverage="measured" if inside == len(times) else "partial",
        open_start=int(times[0] - first[0] < edge) if first else None,
        open_end=int(last[1] - times[-1] < edge) if last else None,
        # A break in the merged coverage inside the window is an interval ending and the next starting.
        coverage_gaps=sum(1 for (_, e), (s, _) in zip(intervals, intervals[1:]) if start <= e and s <= end),
        disconnects=db.execute("SELECT count(*) FROM capture_events WHERE type = 'usb' AND event IN ('disconnected', 'error') "
                               "AND t_ms BETWEEN ? AND ?", (start, end)).fetchone()[0],
        losses=db.execute("SELECT count(*) FROM known_losses WHERE certain = 1 AND start_ms <= ? AND end_ms >= ?", (end, start)).fetchone()[0],
        possible_losses=db.execute("SELECT count(*) FROM known_losses WHERE certain = 0 AND start_ms <= ? AND end_ms >= ?", (end, start)).fetchone()[0])
    return fields


def refresh(db, where="1", params=(), **thresholds):
    """Recompute quality for the sightings matching where (an SQL condition on sightings)."""
    sightings = db.execute(f"SELECT id, aircraft_id, start_ms, end_ms FROM sightings WHERE {where}", params).fetchall()
    for s in sightings:
        times = [r[0] for r in db.execute("SELECT received_ms FROM receptions WHERE aircraft_id = ? AND received_ms BETWEEN ? AND ? "
                                          "ORDER BY received_ms", (s["aircraft_id"], s["start_ms"], s["end_ms"]))]
        fields = sighting_quality(db, s, times, **thresholds)
        db.execute(f"UPDATE sightings SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?", (*fields.values(), s["id"]))
    return len(sightings)
