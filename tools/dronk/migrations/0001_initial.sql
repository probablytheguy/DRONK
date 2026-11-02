-- DRONK Phase 1 schema. Captures (JSONL) are authoritative; everything here is rebuildable from them.
-- Raw receiver lines are never stored here.

CREATE TABLE schema_migrations (
  version     INTEGER PRIMARY KEY,
  description TEXT NOT NULL,
  applied_at  TEXT NOT NULL
);

-- One capture file (live or imported) or one legacy dashboard snapshot.
CREATE TABLE sources (
  id              INTEGER PRIMARY KEY,
  kind            TEXT NOT NULL CHECK (kind IN ('capture', 'dashboard_state')),
  file_name       TEXT NOT NULL,              -- base name only, never a directory path
  file_sha256     TEXT,                       -- set by file import; NULL for a live capture until imported
  capture_id      TEXT UNIQUE,                -- capture_start.capture_id; NULL for dashboard_state
  mode            TEXT,
  synthetic       INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
  started_at      TEXT,
  ended_at        TEXT,
  complete        INTEGER,                    -- capture_end seen
  truncated_tail  INTEGER,
  malformed_lines INTEGER,
  last_record_n   INTEGER,
  needs_reimport  INTEGER NOT NULL DEFAULT 0, -- live indexing dropped or failed; file import will catch up
  imported_at     TEXT NOT NULL
);
CREATE UNIQUE INDEX sources_sha ON sources(file_sha256) WHERE file_sha256 IS NOT NULL AND capture_id IS NULL;

-- A receiver boot session. session_id is 128 random bits per boot; seq restarts at 0 each boot.
CREATE TABLE receiver_sessions (
  id             INTEGER PRIMARY KEY,
  session_id     TEXT NOT NULL UNIQUE,
  receiver_label TEXT NOT NULL,               -- public stream label, never a factory ID or MAC
  first_ms       INTEGER NOT NULL,
  last_ms        INTEGER NOT NULL
);

-- Aircraft identity as broadcast: (identifier_type, identifier). Not ownership or operator.
CREATE TABLE aircraft (
  id               INTEGER PRIMARY KEY,
  identifier_type  TEXT NOT NULL,
  identifier       TEXT NOT NULL,
  synthetic        INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
  first_ms         INTEGER NOT NULL,
  last_ms          INTEGER NOT NULL,
  first_seen       TEXT NOT NULL,
  last_seen        TEXT NOT NULL,
  reception_count  INTEGER NOT NULL DEFAULT 0, -- distinct receptions, never provenance rows
  positioned_count INTEGER NOT NULL DEFAULT 0,
  UNIQUE (identifier_type, identifier, synthetic)
);
CREATE INDEX aircraft_identifier ON aircraft(identifier);
CREATE INDEX aircraft_last ON aircraft(last_ms);

-- Normalized Remote ID receptions: one row per receiver emission, as validated by the bridge.
CREATE TABLE receptions (
  id                  INTEGER PRIMARY KEY,
  dedup_key           TEXT NOT NULL UNIQUE,   -- 'rx:<session_id>:<seq>' or 'legacy:<sha256>' (+ '#<hash>' on a key conflict)
  payload_sha256      TEXT NOT NULL,
  receiver_session_id INTEGER REFERENCES receiver_sessions(id),
  seq                 INTEGER,
  record_kind         TEXT NOT NULL CHECK (record_kind IN ('rid_reception', 'observation', 'tracker_ignored')),
  aircraft_id         INTEGER REFERENCES aircraft(id),  -- NULL without exactly one textual Basic ID
  received_at         TEXT NOT NULL,          -- host receipt UTC, as recorded
  received_ms         INTEGER NOT NULL,
  observed_at         TEXT,
  receiver_label      TEXT,
  transport           TEXT,
  channel             INTEGER,
  frequency_mhz       INTEGER,                -- receiver-reported tuned frequency, not an RF measurement
  rssi_dbm            INTEGER,
  transmitter         TEXT,
  message_counter     INTEGER,
  odid_version        INTEGER,
  messages            TEXT,
  reason              TEXT,
  position_valid      INTEGER NOT NULL CHECK (position_valid IN (0, 1)),
  latitude            REAL,
  longitude           REAL,
  altitude_hae_m      REAL,
  synthetic           INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
  payload_json        TEXT NOT NULL,          -- the validated bridge event, verbatim
  CHECK (position_valid = 1 OR (latitude IS NULL AND longitude IS NULL AND altitude_hae_m IS NULL)),
  CHECK (position_valid = 0 OR (latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180))
);
CREATE INDEX receptions_aircraft_time ON receptions(aircraft_id, received_ms);
CREATE INDEX receptions_time ON receptions(received_ms);

-- Provenance: every capture record that carried a reception. Overlaps add rows here, not receptions.
CREATE TABLE reception_sources (
  reception_id INTEGER NOT NULL REFERENCES receptions(id),
  source_id    INTEGER NOT NULL REFERENCES sources(id),
  record_n     INTEGER NOT NULL,
  record_t     TEXT,
  PRIMARY KEY (source_id, record_n)
);
CREATE INDEX reception_sources_reception ON reception_sources(reception_id);

-- DERIVED: observation windows per aircraft (no silence longer than gap_seconds). Not flights.
CREATE TABLE sightings (
  id                 INTEGER PRIMARY KEY,
  aircraft_id        INTEGER NOT NULL REFERENCES aircraft(id),
  gap_seconds        INTEGER NOT NULL,
  derivation         TEXT NOT NULL,
  start_ms           INTEGER NOT NULL,
  end_ms             INTEGER NOT NULL,
  reception_count    INTEGER NOT NULL,
  positioned_count   INTEGER NOT NULL,
  receivers          TEXT NOT NULL,
  first_reception_id INTEGER NOT NULL REFERENCES receptions(id),
  last_reception_id  INTEGER NOT NULL REFERENCES receptions(id)
);
CREATE INDEX sightings_aircraft ON sightings(aircraft_id, start_ms);

CREATE VIEW aircraft_positions AS
  SELECT id AS reception_id, aircraft_id, received_ms, latitude, longitude, altitude_hae_m, receiver_label
  FROM receptions WHERE position_valid = 1;
