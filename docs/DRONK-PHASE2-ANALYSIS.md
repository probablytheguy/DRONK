# DRONK Phase 2 proposal: historical observation analysis

Status: **steps 2.1–2.3 implemented (2026-11-02), awaiting review; steps 2.4–2.6 (UI) not started.** Builds on Phase 1 (`docs/DRONK-PHASE1-SQLITE.md`). Schema v2.

## Implementation record (2.1–2.3)

### Decisions taken (review of 2026-11-02)

1. **Imports:**
   - Copied into a private `imports/` directory (0700, files 0400), named `<sha256 prefix>-<name>`. The original bytes are kept and verified by size and modification time before and after the copy.
   - The SHA-256 and the original name are recorded on the source.
   - Identical content is stored once, and re-importing is idempotent.
2. **Time:** all timestamps are stored in UTC. They are displayed in a configurable IANA zone, default `America/Chicago`, using `zoneinfo` (historical DST rules). Day, week and month buckets are local; days of 23 and 25 hours are reported as such.
3. **Thresholds:** packet gap 10 s, coverage edge 30 s, status silence 15 s, sighting gap 300 s. All four are configurable and stored in `settings`, and every derived row records the values it used. The packet gap must stay shorter than the sighting gap: it only flags gaps and never splits a sighting.
4. **Limits:**
   - At most 2 GiB per file and 50 files per batch.
   - The aggregate copy size plus 512 MiB must fit in free space, checked before anything is copied.
   - A failed or changing copy leaves no file. `.partial-*` leftovers older than 1 h are removed.
   - A capture still being written is refused.
5. **Operator and take-off positions:**
   - Never part of tracks: the API reports `layers: {aircraft: true, operator: false, takeoff: false}`.
   - Withheld from the provenance view as well; the original bytes stay in the files.
   - The receiver's factory ID (MAC-derived) is withheld from raw lines shown by provenance.

### What was built

- **Migration `0002_observation_quality.sql`** (additive):
  - Columns: `reception_sources.byte_offset`, `sources.stored_name/file_bytes/index_version`, and sighting quality columns.
  - Measured tables `capture_events` and `receiver_lines`; derived tables `coverage` and `known_losses`; `settings`.
  - Before migrating, the backup is read back and must pass `integrity_check` and have the expected `user_version`, otherwise the database is not migrated.
- **Backfill:** `dronk_db.py backfill` re-scans every capture indexed before Phase 2 (`index_version` 1). Receptions are not touched (`already_imported`); offsets, events and lines are added and coverage is derived. A legacy snapshot is backfilled by importing it again, which also stores its copy.
- **Quality (`dronk_quality.py`, `quality-v1`):**
  - **Coverage intervals:** per capture and stream, from receiver protocol lines. They are split at USB events, status silence, receiver reboot (session change), capture end, suspension or interruption.
  - **Known losses:** counter increases between consecutive status lines in one interval.
    - Certain: `usb_output_drops`, `queue_drops`, `oversize_drops`.
    - Possible: `malformed_frames`.
    - Capture-level: suspension, interrupted capture, truncated record.
  - **Buffered statuses excluded:** status pairs where receiver uptime runs more than 2 s ahead of host time are buffered statuses delivered at connect; their counter increases happened while nobody was reading and are not counted.
  - **Sighting quality:** max and median packet gap, long gaps, coverage measured/partial/unknown, open start/end, coverage gaps, disconnects, certain and possible losses.
- **Queries (`dronk_history.py`) and console routes:** `/api/aircraft/summary`, `/sightings`, `/appearances?period=day|week|month`, `/receptions`, `/track`, plus `/api/provenance` and `/api/coverage`. Every route takes `tz=` (an IANA zone).
  - Responses separate `measured` from `derived` and carry their notes.
  - Every timestamp is given as `{utc, local}`.
  - The existing `/api/aircraft` and `/api/aircraft/detail` routes are unchanged.
- **CLI (`dronk_db.py`):**
  - `import` copies, applies the limits and imports.
  - `rebuild` reads `captures/` and `imports/`.
  - `backfill` re-scans pre-Phase 2 captures.
  - `check` verifies integrity plus counts against receptions, sightings against aircraft, provenance, offsets and backfill state; it exits 1 on any problem.
  - `fingerprint` gives a content summary for comparing two databases.
  - Each analysis threshold has a `--<name>` option.
- **Configuration:** TOML `[analysis]` with `time_zone` and the four thresholds. `--time-zone` and `DRONK_TIME_ZONE` override the zone. Unset thresholds keep the values already stored in the database, so the live indexer follows the console.

### Changes from the proposal

- The proposed `receiver_status` became `receiver_lines`, with one row per raw line (type, time, session, seq, uptime, channel, and loss counters for status lines). Reception lines are evidence that the receiver was alive, and they let provenance find the raw line by session and seq through an index rather than by scanning 200 records.
- Sighting windows are still `gap-v1`, because the windowing algorithm didn't change; quality is versioned separately as `quality-v1`.
- `db_error` is recorded as a capture event but is not a loss, because the capture stays complete and the index catches up by re-import.
- Coverage of a sighting is the union over all receivers.
- The HTTP upload endpoint (`POST /api/import`) belongs to step 2.6. The limits and staging it will use are in the store now and used by the CLI.

### Validation

- Python suite: 142 tests (19 new in `tests/test_history.py`). They cover:
  - v1→v2 upgrade of a database reduced to the Phase 1 schema, with a verified backup, backfill and `check`;
  - an upgraded database identical (by fingerprint) to a rebuild;
  - a backup failing verification, which stops the migration;
  - private copies, idempotency, batch, size and free-space limits, interrupted copies and in-progress captures;
  - coverage, losses, buffered statuses, silence and reboot splits, suspension and interruption;
  - threshold changes and persistence, legacy quality unknown;
  - DST days (23 h and 25 h), local midnight, ISO week 53, a fall-back sighting (01:30 CDT to 01:30 CST is 3600 s);
  - tracks without operator data, provenance verification, withholding, missing files, paging;
  - HTTP routes and configuration.
- **Real database (2026-11-02):**
  - Migrated v1→v2 with backup `dronk.sqlite3.bak-v1-20261102T190307.162971Z` (verified: `user_version` 1, `integrity_check` ok, 66 receptions).
  - Backfill: 5 captures, 0 new receptions, 827 receiver lines, 35 capture events, 5 coverage intervals, 4 possible-loss windows (`malformed_frames` +1, +5, +1, +2), 0 certain losses. The +446 USB drops buffered before the first capture were correctly not counted.
  - The legacy snapshot was re-imported into `imports/` (SHA-256 prefix `132e8d0573f2151f`, byte-identical); its provenance now reads `verified`.
  - `check`: ok. A rebuild into a scratch database from `captures/` and `imports/` gave an identical fingerprint; the scratch database was then removed.
  - **Nov 2 Avata: still 66 receptions, 1 aircraft, 1 sighting, 0 positioned.** Observed duration 80.3 s, max packet gap 37.4 s (1 long gap), median 0.64 s. Coverage `unknown`, because legacy data has no capture evidence.

### Known limitations

- Coverage means the receiver was connected and producing lines, not RF coverage of a channel. The hopping receiver dwells 250 ms per channel, so a gap in receptions during measured coverage can still be RF.
- A live session's sightings show coverage `unknown` until the session closes and its capture is imported (at Stop, by the console's sync).
- Coverage and losses are host-time based; status lines are about 5 s apart, so loss windows are about 5 s wide.
- The `malformed_frames` cause is still open; those losses are only "possible".
- Legacy snapshot data stays `unknown` for coverage and losses, and the dashboard log ring may have omitted records.
- Re-deriving quality after a threshold change scans every sighting; this is fine at the current size but has not been measured at scale.
- No UI for any of this yet (2.4–2.6).


## What Phase 1 already gives us

- `receptions` (measured, one per receiver record, with or without position), `aircraft` (identity, first/last seen, counts), `reception_sources` (file + record number), `sightings` (derived, `gap-v1`, threshold stored per row), the `aircraft_positions` view.
- Live indexing, idempotent multi-file import (CLI), `rebuild` from captures, search and a basic detail panel.

## Gaps for Phase 2

1. **Quality and coverage aren't in SQLite.** USB connects/disconnects, `closed_by_host`, receiver reboots, diagnostics (USB output drops, queue drops, malformed frames, stale-baseline resets), `db_error`, `capture_suspended`, truncated tails and missing `capture_end` exist only in the JSONL files. Without them a sighting can't say whether its edges or gaps coincide with the receiver not listening.
2. **Provenance stops at file name + record number.** Showing the original record (and the raw receiver line that produced it) needs a byte offset; today it would mean scanning the file.
3. **Imported legacy files live outside the capture directory**, so `rebuild` only finds them if they're named again on the command line.
4. **No time summaries, no tracks, no import UI.**

## Architecture

Unchanged foundations: JSONL captures are authoritative, SQLite is derived and rebuildable, dedup rules from Phase 1 stay exactly as they are (receiver `session_id`+`seq`, content-conflict keys, legacy content hash, provenance rows for every occurrence).

Two classes of data, kept apart in schema, API and UI:

| Class | Examples | Label in UI |
| --- | --- | --- |
| **Measured** | receptions, their timestamps, RSSI, channel, broadcast positions; capture events (connects, disconnects, counters as reported) | "Observed" / "Reported by receiver" |
| **Derived** | sightings, observed durations, packet gaps, coverage intervals, day/week/month summaries, tracks | "Derived (algorithm vN, threshold X)" |

No flight, landing, patrol or intent inference. A sighting's duration is an **observed duration**: the span between its first and last reception, which is a lower bound on how long the aircraft was transmitting, and says nothing about a flight.

### New stored files

`~/.local/state/dronk/imports/` (0700): every file imported through the CLI or the dashboard is copied here first, read-only (0400), under `<sha256-prefix>-<original name>`. `rebuild` reads `captures/` and `imports/`, so a rebuild reproduces every import. Phase 0 captures already in `captures/` aren't copied.

### Quality model (derived, `quality-v1`)

From capture events per source and receiver:

- **Coverage intervals:** from a stream's `usb connected` (or first status) to its `disconnected` / `error` / `closed_by_host` / `capture_end` / truncated end of an interrupted capture. Within an interval, a status-line silence longer than 3× the status period (15 s) splits it, so a stalled receiver isn't counted as listening.
- **Known losses:** `capture_suspended`, `db_error` (index only; the capture is intact), truncated tail, no `capture_end`, and receiver counter increases during coverage (USB output drops, queue drops, oversize drops), each with its time window between the two diagnostics that bracket it.
- Per sighting:
  - `max_gap_ms`, `median_gap_ms`, and `long_gaps`: the number of gaps between consecutive receptions longer than the packet-gap threshold (default 10 s).
  - `positioned_count` (already present) and `gps`: all, some or none.
  - `open_start` / `open_end`: the sighting starts within 30 s of coverage starting, or ends within 30 s of coverage ending. The aircraft may have been transmitting before or after, so the observed duration is a lower bound.
  - `disconnects` and `losses` overlapping the window, and `receivers`.

Legacy snapshot data has no capture events; its sightings get quality `unknown`, shown as such.

### Time summaries

Computed on query from `receptions` and `sightings` (no stored aggregates, so nothing to keep in sync): per aircraft per day, ISO week and month, giving receptions, sightings that started, positioned receptions and distinct receivers, plus distinct-days and distinct-windows counts for repeat appearances. Days are UTC by default; the API takes an optional fixed UTC offset for display. Stored timestamps stay UTC.

### Geographic history (no external requests)

- Track per sighting from `aircraft_positions`, ordered by time, with each point's reception id (so every point is traceable). Never interpolated; gaps are drawn as gaps.
- Rendered as inline SVG in a local equirectangular projection centered on the track, with a lat/lon grid, scale bar and north arrow; no tiles, fonts or scripts from anywhere else (the existing CSP already forbids them).
- Playback: a time slider and play/pause over the recorded points; the marker jumps between real points and shows each point's time, altitude and source.
- Aircraft positions only. Operator/takeoff positions present in payloads are not shown in Phase 2 unless you decide otherwise (decision 5).

### Provenance

- `reception_sources` gains `byte_offset`, the offset of the record in its file. It's filled by live indexing, by new imports, and by re-importing an already-imported file, which backfills it.
- `GET /api/provenance?reception=<id>` returns, for each occurrence: the source file, record number and timestamp, the stored decoded record read back from the file at that offset, and the raw receiver line that produced it. That raw line is found by matching `session_id` + `seq` within the 200 preceding `rx_line` records. Legacy snapshot occurrences return the snapshot record.
- Files are opened only by source id from the database, and only inside `captures/` or `imports/`, never by a client-supplied path. If a file was moved or deleted, the API says so instead of failing.

## Schema changes: migration 0002

```sql
-- byte offsets for direct provenance reads (NULL until backfilled)
ALTER TABLE reception_sources ADD COLUMN byte_offset INTEGER;

-- where an imported file was stored (imports/<name>); NULL for files in captures/
ALTER TABLE sources ADD COLUMN stored_name TEXT;

-- measured: host/receiver events from captures, one per capture record
CREATE TABLE capture_events (
  id           INTEGER PRIMARY KEY,
  source_id    INTEGER NOT NULL REFERENCES sources(id),
  record_n     INTEGER NOT NULL,
  byte_offset  INTEGER,
  t_ms         INTEGER NOT NULL,
  type         TEXT NOT NULL,     -- usb, diagnostics, receiver_session, capture_suspended, db_error, capture_start, capture_end
  receiver_label TEXT,
  session_id   TEXT,
  detail_json  TEXT NOT NULL,     -- the record's own fields, verbatim
  UNIQUE (source_id, record_n)
);
CREATE INDEX capture_events_time ON capture_events(t_ms);

-- measured: receiver status cadence, compact (one row per status line seen in a capture)
CREATE TABLE receiver_status (
  source_id    INTEGER NOT NULL REFERENCES sources(id),
  record_n     INTEGER NOT NULL,
  t_ms         INTEGER NOT NULL,
  receiver_label TEXT NOT NULL,
  session_id   TEXT,
  uptime_us    INTEGER,
  channel      INTEGER,
  PRIMARY KEY (source_id, record_n)
);
CREATE INDEX receiver_status_time ON receiver_status(receiver_label, t_ms);

-- derived: when each receiver was demonstrably listening (quality-v1)
CREATE TABLE coverage (
  id             INTEGER PRIMARY KEY,
  source_id      INTEGER NOT NULL REFERENCES sources(id),
  receiver_label TEXT NOT NULL,
  session_id     TEXT,
  start_ms       INTEGER NOT NULL,
  end_ms         INTEGER NOT NULL,
  end_reason     TEXT NOT NULL,   -- closed_by_host, disconnected, error, capture_end, status_silence, truncated, unknown
  derivation     TEXT NOT NULL
);
CREATE INDEX coverage_time ON coverage(receiver_label, start_ms);

-- derived: windows where data is known to be missing
CREATE TABLE known_losses (
  id             INTEGER PRIMARY KEY,
  source_id      INTEGER NOT NULL REFERENCES sources(id),
  receiver_label TEXT,
  start_ms       INTEGER NOT NULL,
  end_ms         INTEGER NOT NULL,
  kind           TEXT NOT NULL,   -- usb_output_drops, queue_drops, oversize_drops, capture_suspended, truncated_capture, interrupted_capture
  amount         INTEGER,         -- counter increase, when known
  derivation     TEXT NOT NULL
);
CREATE INDEX known_losses_time ON known_losses(start_ms);

-- derived sighting quality (gap-v2 adds these; thresholds stored per row)
ALTER TABLE sightings ADD COLUMN max_gap_ms INTEGER;
ALTER TABLE sightings ADD COLUMN median_gap_ms INTEGER;
ALTER TABLE sightings ADD COLUMN long_gaps INTEGER;
ALTER TABLE sightings ADD COLUMN packet_gap_seconds INTEGER;
ALTER TABLE sightings ADD COLUMN open_start INTEGER;   -- 1/0, NULL = unknown (no coverage data)
ALTER TABLE sightings ADD COLUMN open_end INTEGER;
ALTER TABLE sightings ADD COLUMN disconnects INTEGER;
ALTER TABLE sightings ADD COLUMN losses INTEGER;
ALTER TABLE sightings ADD COLUMN quality_derivation TEXT;

CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);  -- gap, packet gap, edge margin
```

Migration path for the existing database (v1 → v2):

1. Back up `dronk.sqlite3` (automatic, as in Phase 1).
2. Apply 0002 (adds empty tables and nullable columns; no data rewritten).
3. Re-import every known source idempotently. Receptions are unchanged (`already_imported`), but this backfills byte offsets, capture events and status rows, then derives coverage, losses and `gap-v2` sightings.
4. Cross-check against `rebuild` into a scratch database: the same receptions, aircraft counts and sightings.

## API

| Endpoint | Returns |
| --- | --- |
| `POST /api/import` (multipart, CSRF token, ≤ 2 GiB per file, ≤ 50 files) | Starts a background job: copy into `imports/`, import, derive. Returns a job id. |
| `GET /api/import/<job>` | Per-file progress and results: accepted, duplicate, already imported, conflict, rejected with reasons, other records, complete/truncated. |
| `GET /api/aircraft?q=` | As now, plus distinct days, GPS availability (all/some/none). |
| `GET /api/aircraft/detail?type=&id=` | Summary: first/last seen, receptions, sightings, distinct days, receivers, GPS availability; measured vs. derived sections. |
| `GET /api/aircraft/sightings?type=&id=&before=&limit=` | Windows with observed duration and quality fields. |
| `GET /api/aircraft/summary?type=&id=&period=day\|week\|month&utc_offset=` | Repeat appearances per period. |
| `GET /api/aircraft/receptions?type=&id=&sighting=&before=&limit=` | Paged timeline. |
| `GET /api/aircraft/track?sighting=` | Positioned points only, each with reception id. |
| `GET /api/provenance?reception=` | All occurrences, stored record, raw receiver line. |
| `GET /api/coverage?from=&to=` | Coverage intervals and known losses (for timelines). |

All stay localhost-only behind the existing Host/Origin/Sec-Fetch checks; responses carry no file system paths.

## Dashboard

- New page `/aircraft.html#<type>/<id>` (same static origin, same CSP, vanilla JS). The main console links to it from detections and search results.
  - **Header:** identity; first and last seen; reception, sighting and distinct-day counts; receivers; GPS availability.
  - **Repeat appearances:** a day, week or month table plus a compact calendar strip of the days it appeared.
  - **Sighting timeline:** horizontal bars on a time axis, drawn over the receiver coverage band. Loss and disconnect markers sit inside the bars. Each bar shows its observed duration and quality badges: GPS all/some/none, long gaps, open start or end, losses.
  - **Track playback** for sightings with GPS.
  - **Reception list** with a provenance drawer for each reception.
- **Import page section:** a multi-file picker, upload progress and per-file results, with the duplicate, conflict and rejected counts highlighted.

## Implementation sequence

1. **2.0 (done in this review):** state directory permissions fixed (0700) and directory creation hardened.
2. **2.1 Migration 0002 and backfill:** offsets, capture events, status rows, `imports/` store, `rebuild` over both directories. Tests: v1→v2 upgrade with backup, idempotent backfill, and that rebuild matches an incrementally built database.
3. **2.2 Quality derivation:** coverage, known losses, `gap-v2` sighting quality. Tests: synthetic captures with disconnects, stale silence, suspension, truncation, and counter drops inside and outside sightings.
4. **2.3 Query API:** summaries, sightings, receptions, provenance, coverage. Tests: UTC/offset day boundaries, ISO weeks, provenance from both captures and legacy snapshots, missing-file handling.
5. **2.4 Aircraft page:** header, repeat appearances, sighting timeline, reception list and provenance. Browser checks at desktop and phone width, with no external requests.
6. **2.5 Track playback:** SVG projection and playback. Tests use synthetic positioned captures; real GPS still needs an outdoor flight.
7. **2.6 Multi-file import UI:** upload, background job and results. Tests cover size limits, CSRF, mixed capture, legacy and invalid files, re-upload of the same files, and synthetic files refused.
8. **2.7 Validation:** the full suites, a migration of the real database (with backup), the real receiver capture, and an outdoor Avata run with GPS if available.

Each step is reviewable on its own; 2.1–2.3 change no UI.

## Decisions for review

1. **Imported files:** copy into `imports/` so `rebuild` can always reproduce them (recommended), or reference them where they are.
2. **Day boundaries:** UTC for storage and default display, with an optional fixed offset (recommended), or local time zone by default.
3. **Quality thresholds:** packet gap 10 s (the Avata beaconed about every 1.2 s on 2026-11-02), coverage edge margin 30 s, status silence 15 s.
4. **Upload limits:** 2 GiB per file (the capture file cap) and 50 files per job.
5. **Operator and takeoff positions:** keep them off the Phase 2 track view (recommended; operator location is the most sensitive broadcast field), or show them behind an explicit toggle.
