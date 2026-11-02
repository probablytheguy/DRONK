# DRONK Phase 1 proposal: durable SQLite store

Status: **implemented 2026-11-02, not committed.** Builds on the Phase 0 capture files (`tools/dronk/CAPTURE.md`). The schema actually shipped is `tools/dronk/migrations/0001_initial.sql`; it follows the proposal below with these differences: `aircraft` and `receiver_sessions` also keep epoch-millisecond `first_ms`/`last_ms`; `receptions` keeps `payload_sha256`; `sources` has `needs_reimport`.

### Decisions taken (review of 2026-11-02)

1. Live indexing in the host worker, on its own thread behind a bounded queue (20,000 records). `offer()` never blocks the serial path. A full queue, an unopenable database or a write error only drops index entries (counted); the capture file still holds the records, a `db_error` record is written into the capture, the source is marked `needs_reimport`, and the console's sync imports the file afterwards.
2. Raw receiver lines only in JSONL.
3. Sightings: configurable gap (default 300 s, `--gap-seconds` on the CLI), algorithm `gap-v1`, both stored on every row. Opening the store with a different gap rebuilds the derived table. Sightings are observation windows, not flights.
4. Deduplication by receiver `session_id` + `seq`; content-hash fallback for legacy data (see the ambiguity note below). Every source reference is kept in `reception_sources`.
5. Public receiver label only; receivers are distinguished by label plus per-boot `receiver_sessions`.

### Engineering checks

- **Sequence numbers restart at 0 on every receiver boot** (`main.c`: `static uint64_t sequence`, hello is seq 0). Each boot gets a fresh 128-bit `session_id` from `esp_fill_random`, so (`session_id`, `seq`) is unique per emission. `esp_fill_random` runs before Wi-Fi starts, where ESP-IDF does not promise full entropy; to stay safe if a session ID ever repeated, a key that reappears with **different content** is stored as a separate reception under `<key>#<content hash>` and counted as a `conflict`, never merged.
- **Duplicate captures don't inflate counts.** `aircraft.reception_count` only increases when a new reception row is inserted. A copy of a file (same `capture_id`) reports `already_imported`; a different file holding the same receiver records reports `duplicate` and only adds provenance rows. Tested with three captures plus a copy.
- **Failures and interruptions.** Import commits in batches of 500 inside transactions that also update sightings; an interrupted import keeps its committed batches and a re-run finishes it with no double counting. Live-index failures are recovered by the sync from the capture file. Tested by injected write errors, an unopenable database path, a full queue and an interrupted import.
- **Legacy November 2:** 66 receptions, 1 aircraft (`SERIAL_NUMBER`), 0 with position, every latitude/longitude NULL, 1 sighting of 80.3 s. **Ambiguity:** legacy events carry no receiver sequence, so two byte-identical legacy events would collapse into one; the 66 events all differ (millisecond receipt time and message counter), so none collapsed. Legacy rows can't be matched against later captures.

### Commands

```bash
python3 tools/dronk/dronk_db.py import FILE...      # captures and/or the legacy snapshot; idempotent
python3 tools/dronk/dronk_db.py rebuild [EXTRA...]  # moves the DB aside (kept) and rebuilds from all captures
python3 tools/dronk/dronk_db.py search 1581
python3 tools/dronk/dronk_db.py show SERIAL_NUMBER <id>
python3 tools/dronk/dronk_db.py status
```

The console opens the store at `~/.local/state/dronk/dronk.sqlite3` (`--db`, `DRONK_DB`, TOML `db_path`, `--no-db`; DEMO only with an explicit path), restores aircraft as `STALE`, serves `/api/aircraft?q=` and `/api/aircraft/detail?type=&id=&before=&limit=`, and syncs captures in the background at start and after every host session. A capture without `capture_end` modified in the last 10 s is treated as in use and skipped.

---

The original proposal follows.

## Scope

In Phase 1:

- Durable aircraft identities and per-identity detections (first seen, last seen, reception counts).
- Import of multiple Phase 0 JSONL captures, plus the one pre-Phase 0 dashboard snapshot from 2026-11-02.
- Deduplication by receiver session and sequence, with a documented fallback for older data.
- Positionless receptions kept and searchable like any other.
- Search by Remote ID, per-aircraft timeline.
- Restart recovery: the console reloads detections from the database.
- Sightings: contiguous reception windows per aircraft, as a separate, rebuildable, clearly derived table.

Not in Phase 1: flight-session inference, patrol or behavior classification, pattern analytics, heatmaps, map playback, CSV/JSON export import, PostgreSQL, automatic retention deletion.

## Principles

1. **Captures stay the source of truth.** The database is derived from capture files and can always be rebuilt from them. Raw receiver lines stay in the JSONL files; the database points to them by (`capture_id`, `n`) and does not copy them.
2. **Layers stay separate.** `receptions` are what receivers reported and the bridge validated (one row per receiver emission). `sightings` are inferred from receptions and carry the parameters used. Nothing writes back from an inferred table into a reception.
3. **Never fabricate.** No coordinates without a valid position; no identity without exactly one textual Basic ID; no reception without a source record.
4. **Standard library only.** Python `sqlite3`, WAL mode, foreign keys on, no new dependencies.

## Location and access

- `~/.local/state/dronk/dronk.sqlite3` (same state directory as captures, 0600, directory 0700). Override `--db`, `DRONK_DB`, TOML `db_path`; must be outside the repository.
- DEMO never writes to it. Synthetic data goes to a separate database only when a path is given explicitly, as with captures.
- The HTTP API stays localhost-only with the existing same-origin checks.

## Schema (migration 0001)

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE schema_migrations (
  version     INTEGER PRIMARY KEY,
  description TEXT NOT NULL,
  applied_at  TEXT NOT NULL              -- UTC ISO-8601
);

-- One imported file (or the live capture being written).
CREATE TABLE sources (
  id            INTEGER PRIMARY KEY,
  kind          TEXT NOT NULL CHECK (kind IN ('capture', 'dashboard_state')),
  file_name     TEXT NOT NULL,            -- base name only; no directory paths stored
  file_sha256   TEXT,                     -- NULL while a live capture is still being written
  capture_id    TEXT UNIQUE,              -- from capture_start; NULL for dashboard_state
  mode          TEXT,
  synthetic     INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
  started_at    TEXT,
  ended_at      TEXT,
  complete      INTEGER,                  -- capture_end present
  truncated_tail INTEGER,
  malformed_lines INTEGER,
  last_record_n INTEGER,                  -- resume point for incremental/live ingest
  imported_at   TEXT NOT NULL,
  accepted      INTEGER NOT NULL DEFAULT 0,
  duplicates    INTEGER NOT NULL DEFAULT 0,
  rejected      INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX sources_sha ON sources(file_sha256) WHERE file_sha256 IS NOT NULL;

-- A receiver boot session (firmware session_id). Public label only.
CREATE TABLE receiver_sessions (
  id             INTEGER PRIMARY KEY,
  session_id     TEXT NOT NULL UNIQUE,
  receiver_label TEXT NOT NULL,           -- public stream alias, never a factory ID
  first_seen     TEXT NOT NULL,
  last_seen      TEXT NOT NULL,
  reset_reason   INTEGER
);

-- Aircraft identity: (identifier_type, identifier). Never merged across types.
CREATE TABLE aircraft (
  id              INTEGER PRIMARY KEY,
  identifier_type TEXT NOT NULL,          -- SERIAL_NUMBER, CAA_REGISTRATION_ID, UTM_ASSIGNED_UUID, SPECIFIC_SESSION_ID, ...
  identifier      TEXT NOT NULL,
  synthetic       INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
  first_seen      TEXT NOT NULL,          -- maintained from receptions (host receipt UTC)
  last_seen       TEXT NOT NULL,
  reception_count INTEGER NOT NULL,
  positioned_count INTEGER NOT NULL,      -- receptions with a valid aircraft position
  UNIQUE (identifier_type, identifier, synthetic)
);

-- Raw Remote ID receptions: one row per receiver emission, validated by the bridge.
CREATE TABLE receptions (
  id               INTEGER PRIMARY KEY,
  dedup_key        TEXT NOT NULL UNIQUE,  -- 'rx:<session_id>:<seq>' or 'legacy:<sha256>' (see Deduplication)
  receiver_session_id INTEGER REFERENCES receiver_sessions(id),
  seq              INTEGER,               -- u64 from firmware; rejected if > 2^63-1 (not reachable in practice)
  record_kind      TEXT NOT NULL CHECK (record_kind IN ('rid_reception', 'observation', 'tracker_ignored')),
  aircraft_id      INTEGER REFERENCES aircraft(id),   -- NULL when no single textual Basic ID
  received_at      TEXT NOT NULL,         -- host receipt UTC, as recorded
  received_ms      INTEGER NOT NULL,      -- same instant, epoch ms, for ordering and windows
  observed_at      TEXT,
  receiver_label   TEXT,
  transport        TEXT,
  channel          INTEGER,
  frequency_mhz    INTEGER,               -- receiver-reported tuned frequency, not an RF measurement
  rssi_dbm         INTEGER,
  transmitter      TEXT,
  message_counter  INTEGER,
  odid_version     INTEGER,
  messages         TEXT,                  -- space-separated decoded message types
  reason           TEXT,                  -- e.g. no_valid_position; NULL for tracked observations
  position_valid   INTEGER NOT NULL CHECK (position_valid IN (0, 1)),
  latitude         REAL,
  longitude        REAL,
  altitude_hae_m   REAL,
  synthetic        INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
  payload_json     TEXT NOT NULL,         -- the validated bridge event, verbatim
  CHECK (position_valid = 1 OR (latitude IS NULL AND longitude IS NULL AND altitude_hae_m IS NULL)),
  CHECK (position_valid = 0 OR (latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180))
);
CREATE INDEX receptions_aircraft_time ON receptions(aircraft_id, received_ms);
CREATE INDEX receptions_time ON receptions(received_ms);

-- Provenance: every place a reception was found. Overlapping captures add rows here, not receptions.
CREATE TABLE reception_sources (
  reception_id INTEGER NOT NULL REFERENCES receptions(id),
  source_id    INTEGER NOT NULL REFERENCES sources(id),
  record_n     INTEGER NOT NULL,          -- capture record number (or log index for dashboard_state)
  record_t     TEXT,                      -- the record's own timestamp, unmodified
  PRIMARY KEY (source_id, record_n)
);
CREATE INDEX reception_sources_reception ON reception_sources(reception_id);

-- DERIVED: contiguous reception windows per aircraft. Rebuilt per aircraft; never edited by hand.
CREATE TABLE sightings (
  id              INTEGER PRIMARY KEY,
  aircraft_id     INTEGER NOT NULL REFERENCES aircraft(id),
  gap_seconds     INTEGER NOT NULL,       -- parameter used: a new sighting starts after a longer silence
  derivation      TEXT NOT NULL,          -- e.g. 'gap-v1'
  start_ms        INTEGER NOT NULL,
  end_ms          INTEGER NOT NULL,
  reception_count INTEGER NOT NULL,
  positioned_count INTEGER NOT NULL,
  receivers       TEXT NOT NULL,          -- space-separated labels
  first_reception_id INTEGER NOT NULL REFERENCES receptions(id),
  last_reception_id  INTEGER NOT NULL REFERENCES receptions(id)
);
CREATE INDEX sightings_aircraft ON sightings(aircraft_id, start_ms);

CREATE VIEW aircraft_positions AS     -- geographic points only where valid; no table of tracks yet
  SELECT id AS reception_id, aircraft_id, received_ms, latitude, longitude, altitude_hae_m, receiver_label
  FROM receptions WHERE position_valid = 1;
```

What each table answers:

| Need | Where |
| --- | --- |
| Durable identity and detection, first/last seen, counts | `aircraft` (maintained in the same transaction as each reception insert) |
| Historical receptions without GPS | `receptions` with `position_valid = 0`, `reason` kept |
| Packets vs. sightings | `receptions` (packets) vs. `sightings` (windows); never the same table |
| Traceability | `reception_sources` → `sources` → capture file + record `n` → raw lines in the JSONL |
| Timeline | `receptions` by `aircraft_id, received_ms`, plus `sightings` |

## Deduplication

1. **Receiver key.** `rx:<session_id>:<seq>` for any record carrying both (all Phase 0 captures). Re-importing a capture, importing two overlapping captures, or live ingest followed by a later import of the same file all hit the same key: the reception is stored once, and each sighting of it adds a `reception_sources` row.
2. **Legacy key.** For older data without `session_id`/`seq` (the 2026-11-02 dashboard snapshot): `legacy:` + SHA-256 of the canonical JSON of the validated event (sorted keys, includes the millisecond `received_at`). Exact repeats collapse; distinct receptions stay distinct. These rows have `receiver_session_id` NULL.
3. **File level.** The same file (same SHA-256) is reported as already imported and skipped. A live capture is registered by `capture_id` and resumed from `last_record_n`.
4. **Not in Phase 1:** the same broadcast heard by two receivers (different receiver keys) is two receptions. Matching them by transmitter, `message_counter` and time is a later, derived step.

Import reports per file: accepted (new receptions), duplicate (known key), rejected (with reason: unparseable, failed validation, synthetic mismatch, unknown record kind), skipped (non-Remote ID records such as telemetry). The 2026-11-02 snapshot should import as 66 accepted, 0 duplicate.

## Identity rules

Same as the dashboard today: an identity only for exactly one textual Basic ID of a known type (`no_valid_position` receptions and tracked observations). Receptions with no ID, no textual ID or conflicting IDs are stored with `aircraft_id` NULL and remain searchable by transmitter and time, but are never attached to an aircraft. Identity is not ownership: the schema stores what was broadcast, nothing about who operates it.

## Sightings (derived)

`gap-v1`: sort an aircraft's receptions by `received_ms`; start a new sighting after a silence longer than `gap_seconds` (default 300, configurable; stored on every row). A sighting says "received continuously, with no silence over N seconds", nothing about flights, landings or intent. Changing the parameter or the algorithm version rebuilds that aircraft's sightings. Receptions from all receivers count once each (no cross-receiver merge yet).

## Ingest paths

- **Live:** the host worker, which already writes the capture, inserts each decoded record into SQLite in small batches (one transaction per second). A database error never stops monitoring or the capture: it is logged in the capture as `db_error`, and the dashboard shows the lag. Re-importing the capture later fills the gap idempotently.
- **Import CLI:** `python3 tools/dronk/dronk_db.py import FILE...` (multiple files, any order), `rebuild` (fresh database from every capture in the directory), `aircraft --search TEXT`, `show TYPE ID`.
- **Recovery:** on console start, detections are loaded from `aircraft` plus each aircraft's latest reception, then shown as `STALE` until new receptions arrive. The detection export then includes historical aircraft.

## API and dashboard (Phase 1 minimum)

- `GET /api/aircraft?q=<substring>&limit=` — identity, type, first/last seen, reception and positioned counts, sightings count.
- `GET /api/aircraft/<type>/<identifier>` — summary plus sightings and a paged reception timeline (`before`/`limit`), each with source file and record number.
- Dashboard: search box and an aircraft detail panel with the timeline. No map.

## Migration plan

1. Migrations are numbered SQL files under `tools/dronk/migrations/` (`0001_initial.sql`, …), applied in order inside one transaction each, recorded in `schema_migrations` and mirrored in `PRAGMA user_version`.
2. Before applying any migration to an existing database, a copy is made with the SQLite backup API (`dronk.sqlite3.bak-v<old>-<UTC>`). Backups are never deleted automatically.
3. A database newer than the code refuses to open (no downgrade); the console then runs without the database and says so, and capture continues.
4. Because captures are the source of truth, a damaged or incompatible database can be moved aside and rebuilt with `rebuild`.
5. First rollout: create the database, import the two Phase 0 hardware captures and the 2026-11-02 snapshot, check the counts against the files, then enable live ingest.

## Tests (planned)

- Import three captures that share an aircraft, including two overlapping copies of the same session: receptions counted once, provenance rows for each.
- Re-import the same files: 0 accepted, all duplicate, no row changes.
- Positionless receptions stored with `position_valid = 0`, no coordinates (enforced by CHECK), searchable, counted in `aircraft`.
- Packet count vs. sighting count across a multi-day history with gaps; parameter changes rebuild sightings.
- Restart recovery: detections reload from the database with correct first/last seen and counts.
- Interrupted capture (no `capture_end`, truncated tail) imports its complete records, flagged incomplete; resuming a live capture from `last_record_n`.
- Malformed and synthetic records rejected with reasons; a migration from an empty and a populated v1 database; backup created.
- Legacy snapshot: the 2026-11-02 sanitized fixture imports as one aircraft, 66 receptions, 1 sighting.

## Decisions for review

1. Live ingest in the worker (recommended) vs. a separate process that tails the capture file.
2. Raw receiver lines stay only in JSONL (recommended) vs. also copied into SQLite.
3. Default sighting gap: 300 s (proposed).
4. Legacy dedup by event hash for pre-Phase 0 data (proposed), accepting that those rows can't be matched against later captures.
5. Store the firmware's raw `receiver_id` (contains the board MAC) in the database or only the public label (proposed: label only).
