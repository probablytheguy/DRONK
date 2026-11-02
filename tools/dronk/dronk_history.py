"""DRONK historical queries (Phase 2.3): aircraft summaries, sightings, repeat appearances, receptions,
tracks, provenance and receiver coverage.

Every response keeps MEASURED values (what receivers and the host recorded) apart from DERIVED ones
(computed here or by dronk_quality, labelled with algorithm and parameters). Nothing here infers flights,
patrols, landings or intent. Timestamps are stored in UTC; local times are presentation only, in an IANA
time zone with its historical daylight-saving rules."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dronk_db import DERIVATION, StoreError, _snapshot_records, canonical, iso_ms
import dronk_quality as quality

DEFAULT_TIME_ZONE = "America/Chicago"
MAX_LIMIT = 1000
LEGACY_NOTE = ("Legacy dashboard snapshot: no receiver session or sequence, no capture events, and the dashboard log ring "
               "may have omitted records. Coverage and losses are unknown, not assumed complete.")
DURATION_NOTE = ("Observed duration: time between the first and last reception in the sighting. It is a lower bound on how long "
                 "the aircraft was transmitting near a receiver, not a flight duration.")
COVERAGE_NOTE = ("Coverage: a receiver was connected and producing protocol lines. It is not RF coverage of a channel: a hopping "
                 "receiver visits each channel only part of the time.")
# Fields in stored records or raw receiver lines that carry operator or take-off positions. Withheld from every
# response; the capture files keep the original bytes (their SHA-256 is given so a copy can be verified).
WITHHELD_PREFIXES = ("operator", "takeoff", "pilot")


def zone(name):
    try:
        return ZoneInfo(name or DEFAULT_TIME_ZONE)
    except (ZoneInfoNotFoundError, ValueError):
        raise StoreError("Unknown time zone; use an IANA name such as America/Chicago") from None


def local(ms, tz):
    """ISO 8601 with the zone's offset in effect at that instant (CDT -05:00 or CST -06:00 for Chicago)."""
    return datetime.fromtimestamp(ms / 1000, tz).isoformat(timespec="milliseconds") if ms is not None else None


def stamp(ms, tz):
    return dict(utc=iso_ms(ms), local=local(ms, tz)) if ms is not None else None


def period_of(ms, tz, period):
    """(key, local start, local end) of the day / ISO week / month containing ms, in tz."""
    d = datetime.fromtimestamp(ms / 1000, tz).date()
    if period == "day":
        start, end, key = d, d + timedelta(days=1), d.isoformat()
    elif period == "week":
        year, week, weekday = d.isocalendar()
        start = d - timedelta(days=weekday - 1)
        end, key = start + timedelta(days=7), f"{year}-W{week:02d}"
    elif period == "month":
        start = d.replace(day=1)
        end, key = (start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)), f"{d:%Y-%m}"
    else:
        raise StoreError("period must be day, week or month")
    at = lambda day: datetime(day.year, day.month, day.day, tzinfo=tz)
    return key, at(start), at(end)


def withhold(value):
    """Copy of a record with operator/take-off position fields replaced; strings and flags are kept."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            positional = isinstance(v, (dict, list)) or (isinstance(v, (int, float)) and not isinstance(v, bool))
            out[k] = "[withheld]" if k.lower().startswith(WITHHELD_PREFIXES) and positional else withhold(v)
        return out
    if isinstance(value, list):
        return [withhold(v) for v in value]
    return value


def _limit(limit, default=100):
    limit = default if limit is None else int(limit)
    return max(1, min(limit, MAX_LIMIT))


def _aircraft(store, identifier_type, identifier):
    return store.db.execute("SELECT * FROM aircraft WHERE identifier_type = ? AND identifier = ? AND synthetic = ?",
                            (identifier_type, identifier, int(store.synthetic))).fetchone()


def _parameters(store):
    return dict(store.analysis, sighting_derivation=DERIVATION, quality_derivation=quality.QUALITY)


def _sighting(row, tz):
    measured = dict(first_reception=stamp(row["start_ms"], tz), last_reception=stamp(row["end_ms"], tz),
                    receptions=row["reception_count"], positioned_receptions=row["positioned_count"], receivers=row["receivers"].split())
    known = row["coverage"] not in (None, "unknown")
    derived = dict(
        observed_duration_s=(row["end_ms"] - row["start_ms"]) / 1000, gps=_gps(row["positioned_count"], row["reception_count"]),
        derivation=row["derivation"], gap_seconds=row["gap_seconds"],
        packet_gaps=dict(max_gap_s=_s(row["max_gap_ms"]), median_gap_s=_s(row["median_gap_ms"]), long_gaps=row["long_gaps"],
                         threshold_seconds=row["packet_gap_seconds"]),
        coverage=dict(state=row["coverage"] or "unknown", open_start=_flag(row["open_start"]), open_end=_flag(row["open_end"]),
                      coverage_gaps=row["coverage_gaps"], disconnects=row["disconnects"], losses=row["losses"],
                      possible_losses=row["possible_losses"], edge_seconds=row["edge_seconds"], silence_seconds=row["silence_seconds"])
        if known else dict(state="unknown", reason="no capture evidence covers these receptions (e.g. legacy snapshot or live session not yet closed)"),
        quality_derivation=row["quality_derivation"])
    return dict(id=row["id"], measured=measured, derived=derived)


def _s(ms):
    return ms / 1000 if ms is not None else None


def _flag(v):
    return None if v is None else bool(v)


def _gps(positioned, total):
    return "none" if not positioned else "all" if positioned == total else "some"


def summary(store, identifier_type, identifier, tz):
    a = _aircraft(store, identifier_type, identifier)
    if a is None:
        return None
    db = store.db
    kinds = dict(db.execute(
        "SELECT kinds, count(*) FROM (SELECT r.id, group_concat(DISTINCT s.kind) AS kinds FROM receptions r "
        "JOIN reception_sources p ON p.reception_id = r.id JOIN sources s ON s.id = p.source_id WHERE r.aircraft_id = ? GROUP BY r.id) "
        "GROUP BY kinds", (a["id"],)).fetchall())
    receivers = [r[0] for r in db.execute("SELECT DISTINCT receiver_label FROM receptions WHERE aircraft_id = ? AND receiver_label IS NOT NULL "
                                          "ORDER BY 1", (a["id"],))]
    rows = db.execute("SELECT * FROM sightings WHERE aircraft_id = ?", (a["id"],)).fetchall()
    times = [r[0] for r in db.execute("SELECT received_ms FROM receptions WHERE aircraft_id = ?", (a["id"],))]
    distinct = {p: len({period_of(t, tz, p)[0] for t in times}) for p in ("day", "week", "month")}
    states = defaultdict(int)
    for s in rows:
        states[s["coverage"] or "unknown"] += 1
    return dict(
        aircraft=dict(identifier_type=a["identifier_type"], identifier=a["identifier"], synthetic=bool(a["synthetic"])),
        time_zone=tz.key,
        measured=dict(first_reception=stamp(a["first_ms"], tz), last_reception=stamp(a["last_ms"], tz), receptions=a["reception_count"],
                      positioned_receptions=a["positioned_count"], receivers=receivers,
                      receptions_by_source_kind=dict(capture=sum(v for k, v in kinds.items() if "capture" in k.split(",")),
                                                     legacy_only=kinds.get("dashboard_state", 0))),
        derived=dict(sightings=len(rows), distinct_days=distinct["day"], distinct_weeks=distinct["week"], distinct_months=distinct["month"],
                     gps=_gps(a["positioned_count"], a["reception_count"]),
                     total_observed_duration_s=sum(s["end_ms"] - s["start_ms"] for s in rows) / 1000,
                     sightings_by_coverage=dict(states), parameters=_parameters(store)),
        notes=[DURATION_NOTE, COVERAGE_NOTE] + ([LEGACY_NOTE] if kinds.get("dashboard_state") else []))


def sightings(store, identifier_type, identifier, tz, before_ms=None, limit=None):
    a = _aircraft(store, identifier_type, identifier)
    if a is None:
        return None
    rows = store.db.execute("SELECT * FROM sightings WHERE aircraft_id = ? AND start_ms < ? ORDER BY start_ms DESC LIMIT ?",
                            (a["id"], before_ms if before_ms is not None else 2**62, _limit(limit))).fetchall()
    return dict(time_zone=tz.key, parameters=_parameters(store), sightings=[_sighting(r, tz) for r in rows],
                next_before=rows[-1]["start_ms"] if rows else None, notes=[DURATION_NOTE, COVERAGE_NOTE])


def appearances(store, identifier_type, identifier, tz, period="day"):
    """Repeat appearances per local day / ISO week / month. Counts only; no pattern is inferred."""
    a = _aircraft(store, identifier_type, identifier)
    if a is None:
        return None
    period_of(0, tz, period)  # validates period
    buckets = {}
    def bucket(ms):
        key, start, end = period_of(ms, tz, period)
        if key not in buckets:
            # Elapsed, not wall-clock, hours (aware datetimes in one zone subtract as wall time).
            buckets[key] = dict(period=key, start=start.isoformat(), end=end.isoformat(), hours=(end.timestamp() - start.timestamp()) / 3600,
                                receptions=0, positioned_receptions=0, receivers=set(), sightings_started=0, observed_duration_s=0.0)
        return buckets[key]
    for ms, positioned, receiver in store.db.execute("SELECT received_ms, position_valid, receiver_label FROM receptions WHERE aircraft_id = ?",
                                                     (a["id"],)):
        b = bucket(ms)
        b["receptions"] += 1
        b["positioned_receptions"] += positioned
        if receiver:
            b["receivers"].add(receiver)
    for start, end in store.db.execute("SELECT start_ms, end_ms FROM sightings WHERE aircraft_id = ?", (a["id"],)):
        b = bucket(start)
        b["sightings_started"] += 1
        b["observed_duration_s"] += (end - start) / 1000
    rows = [dict(b, receivers=sorted(b["receivers"])) for _, b in sorted(buckets.items())]
    return dict(time_zone=tz.key, period=period, periods=rows, distinct_periods=len(rows),
                notes=["Measured: receptions, positioned receptions, receivers. Derived: sightings_started and observed_duration_s "
                       "(each sighting is counted in the period it started, even when it continues into the next).",
                       "Periods are local to the time zone; days of 23 or 25 hours occur at daylight-saving changes."])


def receptions(store, identifier_type, identifier, tz, sighting_id=None, before_ms=None, limit=None):
    a = _aircraft(store, identifier_type, identifier)
    if a is None:
        return None
    low, high = -2**62, 2**62
    if sighting_id is not None:
        s = store.db.execute("SELECT start_ms, end_ms FROM sightings WHERE id = ? AND aircraft_id = ?", (int(sighting_id), a["id"])).fetchone()
        if s is None:
            return None
        low, high = s["start_ms"], s["end_ms"]
    rows = store.db.execute(
        "SELECT r.*, (SELECT json_group_array(json_object('source', s.id, 'kind', s.kind, 'file', s.file_name, 'record', p.record_n)) "
        "FROM reception_sources p JOIN sources s ON s.id = p.source_id WHERE p.reception_id = r.id) AS occurrences "
        "FROM receptions r WHERE aircraft_id = ? AND received_ms BETWEEN ? AND ? AND received_ms < ? "
        "ORDER BY received_ms DESC, id DESC LIMIT ?",
        (a["id"], low, high, before_ms if before_ms is not None else 2**62, _limit(limit))).fetchall()
    out = []
    for r in rows:
        occurrences = json.loads(r["occurrences"])
        out.append(dict(
            id=r["id"], received=stamp(r["received_ms"], tz), observed_at=r["observed_at"], record_kind=r["record_kind"],
            receiver=r["receiver_label"], transport=r["transport"], channel=r["channel"], frequency_mhz=r["frequency_mhz"],
            rssi_dbm=r["rssi_dbm"], message_counter=r["message_counter"], messages=r["messages"].split() if r["messages"] else None,
            reason=r["reason"], position=dict(latitude=r["latitude"], longitude=r["longitude"], altitude_hae_m=r["altitude_hae_m"])
            if r["position_valid"] else None, sequence=dict(session_known=r["receiver_session_id"] is not None, seq=r["seq"]),
            legacy=all(o["kind"] == "dashboard_state" for o in occurrences), occurrences=occurrences))
    return dict(time_zone=tz.key, receptions=out, next_before=rows[-1]["received_ms"] if rows else None,
                notes=["All fields are measured: as reported by the receiver and validated by the bridge. frequency_mhz is the "
                       "receiver's tuned frequency, not an RF measurement.", LEGACY_NOTE])


def track(store, identifier_type, identifier, tz, sighting_id=None):
    """Broadcast aircraft positions only, in time order, each traceable to its reception. Never interpolated."""
    a = _aircraft(store, identifier_type, identifier)
    if a is None:
        return None
    low, high = -2**62, 2**62
    if sighting_id is not None:
        s = store.db.execute("SELECT start_ms, end_ms FROM sightings WHERE id = ? AND aircraft_id = ?", (int(sighting_id), a["id"])).fetchone()
        if s is None:
            return None
        low, high = s["start_ms"], s["end_ms"]
    points = store.db.execute("SELECT reception_id, received_ms, latitude, longitude, altitude_hae_m, receiver_label FROM aircraft_positions "
                              "WHERE aircraft_id = ? AND received_ms BETWEEN ? AND ? ORDER BY received_ms, reception_id LIMIT 20000",
                              (a["id"], low, high)).fetchall()
    total = store.db.execute("SELECT count(*) FROM receptions WHERE aircraft_id = ? AND received_ms BETWEEN ? AND ?",
                             (a["id"], low, high)).fetchone()[0]
    return dict(time_zone=tz.key, points=[dict(reception_id=p["reception_id"], received=stamp(p["received_ms"], tz), latitude=p["latitude"],
                                               longitude=p["longitude"], altitude_hae_m=p["altitude_hae_m"], receiver=p["receiver_label"]) for p in points],
                receptions_without_position=total - len(points), layers=dict(aircraft=True, operator=False, takeoff=False),
                notes=["Aircraft positions as broadcast, unverified. Operator and take-off locations are not part of tracks.",
                       "Points are never interpolated; time between points is a gap in the record, not a known path."])


def coverage(store, from_ms, to_ms, tz):
    if to_ms < from_ms or to_ms - from_ms > 366 * 86_400_000:
        raise StoreError("Coverage range must be forward and at most 366 days")
    db = store.db
    intervals = db.execute("SELECT c.*, s.file_name FROM coverage c JOIN sources s ON s.id = c.source_id WHERE end_ms >= ? AND start_ms <= ? "
                           "ORDER BY start_ms LIMIT 5000", (from_ms, to_ms)).fetchall()
    losses = db.execute("SELECT l.*, s.file_name FROM known_losses l JOIN sources s ON s.id = l.source_id WHERE end_ms >= ? AND start_ms <= ? "
                        "ORDER BY start_ms LIMIT 5000", (from_ms, to_ms)).fetchall()
    events = db.execute("SELECT e.t_ms, e.type, e.stream, e.event, s.file_name FROM capture_events e JOIN sources s ON s.id = e.source_id "
                        "WHERE (e.type = 'usb' OR e.type IN ('capture_start', 'capture_end', 'capture_suspended', 'receiver_session', 'db_error')) "
                        "AND t_ms BETWEEN ? AND ? ORDER BY t_ms LIMIT 5000", (from_ms, to_ms)).fetchall()
    legacy = db.execute("SELECT count(*) FROM receptions r WHERE received_ms BETWEEN ? AND ? AND NOT EXISTS (SELECT 1 FROM reception_sources p "
                        "JOIN sources s ON s.id = p.source_id WHERE p.reception_id = r.id AND s.kind = 'capture')", (from_ms, to_ms)).fetchone()[0]
    return dict(
        time_zone=tz.key, range=dict(start=stamp(from_ms, tz), end=stamp(to_ms, tz)),
        measured=dict(events=[dict(at=stamp(e["t_ms"], tz), type=e["type"], stream=e["stream"], event=e["event"], file=e["file_name"]) for e in events]),
        derived=dict(
            coverage=[dict(stream=c["stream"], start=stamp(c["start_ms"], tz), end=stamp(c["end_ms"], tz), start_reason=c["start_reason"],
                           end_reason=c["end_reason"], lines=c["line_count"], silence_seconds=c["silence_seconds"], derivation=c["derivation"],
                           file=c["file_name"]) for c in intervals],
            known_losses=[dict(stream=l["stream"], start=stamp(l["start_ms"], tz), end=stamp(l["end_ms"], tz), kind=l["kind"], amount=l["amount"],
                               certain=bool(l["certain"]), evidence=l["evidence"], derivation=l["derivation"], file=l["file_name"]) for l in losses]),
        receptions_without_capture_evidence=legacy, notes=[COVERAGE_NOTE] + ([LEGACY_NOTE] if legacy else []))


def provenance(store, reception_id, tz):
    """Every file record that carried this reception, read back from the stored file and checked against it,
    plus the raw receiver line it came from when the capture holds it."""
    db = store.db
    r = db.execute("SELECT id, dedup_key, payload_sha256, receiver_session_id, seq, received_ms FROM receptions WHERE id = ?",
                   (int(reception_id),)).fetchone()
    if r is None:
        return None
    session = db.execute("SELECT session_id FROM receiver_sessions WHERE id = ?", (r["receiver_session_id"],)).fetchone() if r["receiver_session_id"] else None
    occurrences = []
    for o in db.execute("SELECT p.record_n, p.record_t, p.byte_offset, s.* FROM reception_sources p JOIN sources s ON s.id = p.source_id "
                        "WHERE p.reception_id = ? ORDER BY s.id, p.record_n", (r["id"],)):
        item = dict(source=dict(id=o["id"], kind=o["kind"], file=o["file_name"], sha256=o["file_sha256"], stored_copy=o["stored_name"] is not None,
                                complete=_flag(o["complete"]), capture_id=o["capture_id"]),
                    record=o["record_n"], record_time=o["record_t"], byte_offset=o["byte_offset"])
        path, problem = _located(store, o)
        if problem:
            item["status"] = problem
        elif o["kind"] == "dashboard_state":
            item.update(_snapshot_record(path, o["record_n"], r["payload_sha256"]), note=LEGACY_NOTE)
        else:
            item.update(_capture_record(path, o, r["payload_sha256"]))
            raw = db.execute("SELECT record_n, byte_offset FROM receiver_lines WHERE source_id = ? AND session_id = ? AND seq = ? AND record_n < ? "
                             "ORDER BY record_n DESC LIMIT 1", (o["id"], session[0], r["seq"], o["record_n"])).fetchone() if session else None
            item["raw_line"] = _raw_line(path, o["capture_id"], raw, session[0], r["seq"]) if raw else dict(
                status="not_found", reason="no receiver line with this session and sequence in the capture" if session else "no receiver sequence")
        occurrences.append(item)
    return dict(reception_id=r["id"], dedup_key=r["dedup_key"], payload_sha256=r["payload_sha256"], received=stamp(r["received_ms"], tz),
                occurrences=occurrences, withheld=list(WITHHELD_PREFIXES),
                notes=["Records are read from the stored files and checked against the database; operator and take-off position fields "
                       "are withheld from this view. The original bytes stay in the files; their SHA-256 is given."])


def _located(store, source):
    """The stored file for a source, only ever inside the imports or capture directory."""
    if source["stored_name"]:
        base, name = store.imports, source["stored_name"]
    elif source["kind"] == "capture" and store.captures is not None:
        base, name = store.captures, source["file_name"]
    else:
        return None, "not_stored"  # imported before Phase 2 from elsewhere: re-import the file to keep a copy
    path = base / name
    if Path(name).name != name or not path.resolve().is_relative_to(base.resolve()):
        return None, "not_stored"
    return (path, None) if path.is_file() else (None, "file_missing")


def _capture_record(path, source, payload_sha256):
    if source["byte_offset"] is None:
        return dict(status="offset_unknown", reason="indexed before Phase 2; run the backfill")
    with path.open("rb") as f:
        f.seek(source["byte_offset"])
        raw = f.readline()
    try:
        record = json.loads(raw)
    except ValueError:
        return dict(status="file_changed")
    if not isinstance(record, dict) or record.get("n") != source["record_n"] or record.get("capture_id") != source["capture_id"]:
        return dict(status="file_changed")
    event = record.get("event")
    matches = hashlib.sha256(canonical(event).encode()).hexdigest() == payload_sha256
    return dict(status="verified" if matches else "content_differs", stored_record=withhold(record))


def _snapshot_record(path, record_n, payload_sha256):
    try:
        records = _snapshot_records(path, dict(other_records=0))
    except StoreError:
        return dict(status="file_changed")
    entry = next((e for n, _, e in records if n == record_n), None)
    if entry is None:
        return dict(status="file_changed")
    matches = hashlib.sha256(canonical(entry).encode()).hexdigest() == payload_sha256
    return dict(status="verified" if matches else "content_differs", stored_record=withhold(entry))


def _raw_line(path, capture_id, row, session_id, seq):
    with path.open("rb") as f:
        f.seek(row["byte_offset"])
        raw = f.readline()
    try:
        record = json.loads(raw)
        assert isinstance(record, dict) and record.get("n") == row["record_n"] and record.get("capture_id") == capture_id
    except (ValueError, AssertionError):
        return dict(status="file_changed")
    line = record.get("line")
    if not isinstance(line, str):
        return dict(status="binary", record=row["record_n"])
    try:
        parsed = json.loads(line)
    except ValueError:
        return dict(status="file_changed")
    if not isinstance(parsed, dict) or parsed.get("session_id") != session_id or parsed.get("seq") != seq:
        return dict(status="file_changed")
    if "receiver_id" in parsed:
        parsed["receiver_id"] = "[withheld: receiver factory ID]"  # the database keeps the public stream label only
    parsed = withhold(parsed)
    return dict(status="verified", record=row["record_n"], received_at=record.get("t"), stream=record.get("stream"),
                line_sha256=hashlib.sha256(line.encode()).hexdigest(), line=parsed,
                note="Raw receiver output, parsed for display with operator and take-off fields withheld.")


def markers(store, identifier_type, identifier, tz, limit=None):
    """The bridge's LIVE / LAST_KNOWN marker transitions for an aircraft and its operator/take-off markers. Positions
    are each role's own last valid broadcast position; kept after TAK clients drop the markers."""
    a = _aircraft(store, identifier_type, identifier)
    if a is None:
        return None
    rows = store.db.execute("SELECT m.*, s.file_name FROM marker_states m JOIN sources s ON s.id = m.source_id WHERE m.identifier_type = ? "
                            "AND m.identifier = ? AND m.synthetic = ? ORDER BY coalesce(reported_ms, last_seen_ms) DESC LIMIT ?",
                            (identifier_type, identifier, int(store.synthetic), _limit(limit))).fetchall()
    return dict(time_zone=tz.key, markers=[dict(
        uid=m["uid"], role=m["role"], state=m["state"], reason=m["reason"], last_seen=stamp(m["last_seen_ms"], tz),
        reported=stamp(m["reported_ms"], tz), stale=stamp(m["stale_ms"], tz), stream=m["stream"],
        position=dict(latitude=m["latitude"], longitude=m["longitude"], altitude_hae_m=m["altitude_hae_m"]) if m["latitude"] is not None else None,
        file=m["file_name"], record=m["record_n"]) for m in rows],
        notes=["LAST_KNOWN: no broadcast refreshed that marker for the stale interval. receiver_interrupted means the receiver lost "
               "coverage, so the aircraft may still have been transmitting; no_receptions means the receiver stayed available."])
