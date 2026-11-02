-- DRONK Phase 2: capture-quality evidence (measured) and observation quality (derived).
-- Additive only: no existing row is rewritten. The re-import backfill (dronk_db.py backfill) fills the new
-- columns and tables from the capture files, which stay authoritative. Raw receiver lines are still never stored.

-- Byte offset of the record in its file, for direct provenance reads. NULL for legacy snapshots and until backfilled.
ALTER TABLE reception_sources ADD COLUMN byte_offset INTEGER;

ALTER TABLE sources ADD COLUMN stored_name   TEXT;                        -- private copy in imports/; NULL for files read in captures/
ALTER TABLE sources ADD COLUMN file_bytes    INTEGER;
ALTER TABLE sources ADD COLUMN index_version INTEGER NOT NULL DEFAULT 1;  -- 2: offsets, capture events and receiver lines indexed

-- MEASURED: host and receiver events recorded in a capture, verbatim apart from the envelope fields.
CREATE TABLE capture_events (
  source_id   INTEGER NOT NULL REFERENCES sources(id),
  record_n    INTEGER NOT NULL,
  byte_offset INTEGER NOT NULL,
  t_ms        INTEGER NOT NULL,
  type        TEXT NOT NULL CHECK (type IN ('capture_start', 'usb', 'diagnostics', 'receiver_session', 'capture_suspended', 'db_error', 'capture_end')),
  stream      TEXT,
  event       TEXT,                     -- usb: connected / disconnected / error / closed_by_host
  session_id  TEXT,
  detail_json TEXT NOT NULL,
  PRIMARY KEY (source_id, record_n)
);
CREATE INDEX capture_events_time ON capture_events(type, t_ms);

-- MEASURED: one row per raw receiver line in a capture: when it arrived and what kind of line it was. The line
-- itself stays in the capture; only its type and, for protocol lines, the receiver's session, sequence, uptime,
-- tuned channel and (status lines) loss counters are kept. The receiver's factory ID is not kept.
CREATE TABLE receiver_lines (
  source_id   INTEGER NOT NULL REFERENCES sources(id),
  record_n    INTEGER NOT NULL,
  byte_offset INTEGER NOT NULL,
  t_ms        INTEGER NOT NULL,
  stream      TEXT NOT NULL,
  line_type   TEXT NOT NULL,            -- status / reception / observation / other protocol type / empty / unparsed / fragment
  session_id  TEXT,
  seq         INTEGER,
  uptime_us   INTEGER,
  channel     INTEGER,
  counters    TEXT,                     -- status only: JSON of the loss counters
  PRIMARY KEY (source_id, record_n)
);
CREATE INDEX receiver_lines_stream_time ON receiver_lines(stream, t_ms);
CREATE INDEX receiver_lines_seq ON receiver_lines(source_id, session_id, seq);

-- DERIVED: intervals in which a receiver was demonstrably connected and producing protocol lines. Not RF
-- coverage of any particular channel: a hopping receiver visits each channel only part of the time.
CREATE TABLE coverage (
  id              INTEGER PRIMARY KEY,
  source_id       INTEGER NOT NULL REFERENCES sources(id),
  stream          TEXT NOT NULL,
  session_id      TEXT,
  start_ms        INTEGER NOT NULL,
  end_ms          INTEGER NOT NULL,
  start_reason    TEXT NOT NULL,        -- connected / resumed / receiver_reboot / first_line
  end_reason      TEXT NOT NULL,        -- closed_by_host / disconnected / error / status_silence / receiver_reboot / capture_end / capture_suspended / interrupted
  line_count      INTEGER NOT NULL,
  silence_seconds INTEGER NOT NULL,
  derivation      TEXT NOT NULL,
  CHECK (end_ms >= start_ms)
);
CREATE INDEX coverage_time ON coverage(start_ms, end_ms);
CREATE INDEX coverage_source ON coverage(source_id);

-- DERIVED: windows in which data is known (certain = 1) or suspected (certain = 0) to be missing.
CREATE TABLE known_losses (
  id         INTEGER PRIMARY KEY,
  source_id  INTEGER NOT NULL REFERENCES sources(id),
  stream     TEXT,                      -- NULL: the whole capture
  session_id TEXT,
  start_ms   INTEGER NOT NULL,
  end_ms     INTEGER NOT NULL,
  kind       TEXT NOT NULL,             -- usb_output_drops / queue_drops / oversize_drops / malformed_frames / capture_suspended / interrupted_capture / truncated_record
  amount     INTEGER,                   -- counter increase when known
  certain    INTEGER NOT NULL CHECK (certain IN (0, 1)),
  evidence   TEXT NOT NULL,             -- receiver_counters / capture_marker / file_state
  derivation TEXT NOT NULL,
  CHECK (end_ms >= start_ms)
);
CREATE INDEX known_losses_time ON known_losses(start_ms, end_ms);
CREATE INDEX known_losses_source ON known_losses(source_id);

-- DERIVED sighting quality (quality-v1). The windows themselves are unchanged (gap-v1). NULL where the
-- evidence does not exist, e.g. legacy snapshots without capture events: unknown, never assumed good.
ALTER TABLE sightings ADD COLUMN max_gap_ms         INTEGER;
ALTER TABLE sightings ADD COLUMN median_gap_ms      INTEGER;
ALTER TABLE sightings ADD COLUMN long_gaps          INTEGER;
ALTER TABLE sightings ADD COLUMN packet_gap_seconds INTEGER;
ALTER TABLE sightings ADD COLUMN coverage           TEXT;     -- measured (every reception inside coverage) / partial / unknown
ALTER TABLE sightings ADD COLUMN open_start         INTEGER;  -- 1: within edge_seconds of coverage starting; may have begun earlier
ALTER TABLE sightings ADD COLUMN open_end           INTEGER;
ALTER TABLE sightings ADD COLUMN coverage_gaps      INTEGER;  -- breaks in coverage inside the window
ALTER TABLE sightings ADD COLUMN disconnects        INTEGER;
ALTER TABLE sightings ADD COLUMN losses             INTEGER;  -- certain loss windows overlapping
ALTER TABLE sightings ADD COLUMN possible_losses    INTEGER;
ALTER TABLE sightings ADD COLUMN edge_seconds       INTEGER;
ALTER TABLE sightings ADD COLUMN silence_seconds    INTEGER;
ALTER TABLE sightings ADD COLUMN quality_derivation TEXT;

-- Analysis parameters in effect; every derived row also records the ones it was computed with.
CREATE TABLE settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
