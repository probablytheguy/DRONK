-- DRONK: outbound marker lifecycle (LIVE / LAST_KNOWN) as reported by the bridge, one row per transition.
-- MEASURED in the sense that these are the bridge's own records in the capture; the positions are the role's last
-- valid broadcast position (never derived), so historical last-known positions survive after TAK drops a marker.

CREATE TABLE marker_states (
  source_id       INTEGER NOT NULL REFERENCES sources(id),
  record_n        INTEGER NOT NULL,
  byte_offset     INTEGER,
  aircraft_id     INTEGER REFERENCES aircraft(id),
  identifier_type TEXT NOT NULL,
  identifier      TEXT NOT NULL,
  uid             TEXT NOT NULL,           -- CoT UID (aircraft, or aircraft UID + -operator / -takeoff)
  role            TEXT NOT NULL CHECK (role IN ('aircraft', 'operator', 'takeoff')),
  state           TEXT NOT NULL CHECK (state IN ('LIVE', 'LAST_KNOWN')),
  reason          TEXT NOT NULL,           -- no_receptions / receiver_interrupted / monitoring_stopped / receptions_resumed
  last_seen_ms    INTEGER NOT NULL,        -- the role's last broadcast observation, not the report time
  reported_ms     INTEGER,
  stale_ms        INTEGER,
  stream          TEXT,
  latitude        REAL,
  longitude       REAL,
  altitude_hae_m  REAL,
  synthetic       INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
  PRIMARY KEY (source_id, record_n),
  CHECK ((latitude IS NULL) = (longitude IS NULL))
);
CREATE INDEX marker_states_aircraft ON marker_states(aircraft_id, last_seen_ms);
