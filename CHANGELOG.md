# Changelog

Newest first. Hardware results are listed only where they were run on the board.

## Unreleased

### 2026-11-02: first public source snapshot

- New top-level README for DRONK, built from the public project introduction, with field-verified and software-only features listed separately. The earlier Android prototype README moved to `docs/ANDROID-PROTOTYPE.md`.
- Added `THIRD_PARTY_NOTICES.md` and `SECURITY.md`.
- Removed the unused vendored Leaflet copy (the dashboard map was removed earlier).
- Firmware README: hardware status updated for hopping and the Remote ID field test.
- Published to `probablytheguy/DRONK` as a single snapshot commit, without private history.

### 2026-11-02: first end-to-end field test, LIVE TAK, operator markers, LAST KNOWN

- **Verified in the field:**
  - First real aircraft GPS positions from the Avata, on the ground: 163 positioned receptions. Not yet compared with an independent GPS fix.
  - Offline CoT replay of that session: 162 events, all fields matching.
  - Authenticated TLS to the TAK server, through its local address.
  - LIVE forwarding: the server redistributed the aircraft and operator events, and iTAK displayed both markers.
  - Details: `docs/DRONK-JOURNAL.md`.
- **Operator and take-off markers:** separate CoT events with their own UIDs (`…-operator`, `…-takeoff`), only from a valid broadcast position, linked to the aircraft. Shared core and both Linux bridges.
- **LAST KNOWN:**
  - **Trigger and content:** one update on the same UID 60 s after a marker's last refresh, using a periodic check. It carries the last broadcast position, a grey spot, a `LAST KNOWN` label, the real last-seen time, and a 10-minute stale time. The operator ages independently.
  - **Reasons:** `no_receptions`, `receiver_interrupted` or `monitoring_stopped`. On Stop, the final reports are flushed.
  - **Resuming:** live tracking returns on the same UIDs.
  - **Storage:** new migration `0003_marker_states.sql`, `/api/aircraft/markers`, and a dashboard state *Last known (reason)*.
  - **Status:** not yet verified in iTAK.
- **Symbology:** rotary-wing drone `a-u-A-M-H-Q` and fixed-wing drone `a-u-A-M-F-Q` from the broadcast UA type, operator `a-u-G`, callsigns from the serial's last four characters (full serial in metadata). New bridge options `--last-known-seconds` and `--last-known-style spot|same-type`. Not yet verified in iTAK.
- **Fix:** the TAK card no longer shows `CONNECTING` on every write.
- **Open:** one receiver stall followed by a USB drop-out in the field, cause unknown.
- **Tests:** Python 145, Linux bridge 39, Android unit 78; all pass. No firmware changes.

### 2026-11-02: Phase 2.1–2.3, historical observation analysis (software; real database migrated)

- **Schema v2** (`migrations/0002_observation_quality.sql`, additive):
  - Byte offsets for provenance and private stored copies of imports.
  - Measured `capture_events` and `receiver_lines`; derived `coverage`, `known_losses` and sighting-quality columns; `settings`.
  - The migration backs up first and verifies the backup before changing anything.
- **Backfill:** `dronk_db.py backfill` re-scans pre-Phase 2 captures without changing receptions. `check` and `fingerprint` verify integrity and compare databases.
- **Imports:** copied into `~/.local/state/dronk/imports/` (0700; files 0400, SHA-256 named), idempotent. Limits: 2 GiB per file, 50 files per batch, aggregate free space checked first. Interrupted copies leave nothing behind; captures still being written are refused.
- **Quality (`quality-v1`):** receiver coverage intervals, certain and possible losses from receiver counters (buffered pre-connect statuses excluded), and per-sighting packet gaps, open start/end, coverage gaps, disconnects and losses. Thresholds are configurable (10 s, 30 s, 15 s; sighting gap 300 s) and recorded per row. Legacy data stays `unknown`.
- **History API:** summary, sightings, day/week/month appearances, receptions, tracks, provenance and coverage. Measured and derived values are kept separate; times are given in UTC plus a configurable IANA zone (default America/Chicago, DST-correct). Tracks exclude operator and take-off positions; provenance withholds them and the receiver factory ID.
- **Real database:** migrated v1→v2 with a verified backup; backfill of 5 captures; legacy snapshot stored. The Nov 2 Avata data is still 66 receptions, 1 aircraft, 1 sighting. A rebuild matched the upgraded database exactly.
- **Docs:** outdoor GPS and TAK validation checklist (`docs/DRONK-OUTDOOR-GPS-TAK-CHECKLIST.md`, not run). `~/.local/state/ouitak` tightened to 0700.
- **Tests:** 19 new Python tests (142 total). No firmware, Kotlin, UI or TAK changes.

### 2026-11-02: Phase 1, SQLite aircraft history (software; real receiver run without aircraft)

- **Store:** `tools/dronk/dronk_db.py`, schema v1 (`migrations/0001_initial.sql`): sources, receiver sessions, aircraft identities, normalized receptions (with or without position), provenance, derived sightings (`gap-v1`, 300 s default, recorded per row). Raw lines stay in JSONL only.
- **Live index:** host worker thread with a bounded queue; never blocks serial reception; failures write `db_error` into the capture and are recovered by re-import. The console syncs captures at start and after each session.
- **Import:** multiple capture files and the legacy dashboard snapshot; idempotent; resumable; dedup by receiver `session_id`+`seq` (content conflicts kept apart), content hash for legacy data. `rebuild` recreates the database from captures and keeps the old one.
- **Console:** restores aircraft as `STALE`, aircraft history search and detail (first/last seen, receptions, sightings, timeline with provenance).
- **Migrations:** numbered SQL, backup before upgrading an existing database, refuse newer schemas, failed migrations roll back.
- **Data:** the 2026-11-02 Avata snapshot imports as 66 receptions, 1 aircraft, 0 positioned, no coordinates.
- **Tests:** 22 new Python tests. No firmware or TAK changes.

### 2026-11-02: Phase 0, durable capture (software only)

- **Capture files:** every started session writes a new append-only JSONL capture (`tools/dronk/CAPTURE.md`): raw receiver lines before validation, validated Remote ID records (including positionless receptions), telemetry, USB events, explicit `capture_start`/`capture_end` boundaries. `O_EXCL` unique names, so restarts never overwrite; fsync every 2 s; monitoring refuses to start without a capture; writing suspends below 512 MiB free or at 2 GiB. New options `--capture-dir`, `--no-capture`, `DRONK_CAPTURE_DIR`, TOML `capture_dir`.
- **Diagnostics:** session-relative deltas for USB output drops, malformed frames, queue drops, oversize drops, output errors and USB disconnects, in the capture and on the dashboard. Receiver reboots are recorded as `receiver_session`.
- **Provenance:** bridge `rid_reception` events now carry the receiver `session_id` and `seq` (additive; older captures without them still load). The dashboard counts a repeated (`session_id`, `seq`) once.
- **Fix:** pressing Stop while the bridge is still starting is a clean stop, not a worker failure.
- **Unchanged:** detection states, export formats, synthetic isolation and TAK forwarding.
- **Hardware validation (2 CAPTURE sessions, no aircraft):** files complete and readable after a console restart; 0 USB output drops during capture; malformed +1 and +5. Fixes from it: stale buffered statuses on connect no longer become the diagnostics baseline; a requested stop is `closed_by_host`, not a disconnect; the dashboard shows the capture `CLOSED` after Stop.
- **Tests:** 27 new Python tests (capture writer, restart, duplicates, malformed input, interruption, disk limits, config) and 1 Kotlin test. Not yet run with the real receiver.

### 2026-11-02: first real Remote ID, detections without GPS

- **Field test (hardware):** first real, nonsynthetic Remote ID received. Original DJI Avata, indoors, no GPS fix: 66 decoded Wi-Fi Beacon packs on channel 149 (5745 MHz, receiver-reported), ODID v2, Basic ID + Location + System, RSSI −60 to −44 dBm, all `no_valid_position`. TAK forwarding disabled. Not field-verified: aircraft coordinates, tracking, TAK forwarding. See `docs/C5-HARDWARE-BRINGUP.md#field-results-2026-11-02-first-real-remote-id`.
- **DRONK Control:** aircraft detection records separate identity from position. New states `DETECTED_NO_POSITION`, `TRACKING`, `STALE`; dashboard panel *Aircraft detections*; positionless aircraft are never tracks or TAK markers.
- **Export compatibility change:** `/api/export` now returns aircraft detections (new CSV columns, JSON array of detection objects). The previous per-observation export is at `/api/export?format=csv|json&table=history`, unchanged.
- **Tests:** 13 new DRONK Control tests, including a replay of the field session with identifiers replaced.

### 2026-11-01: Phase 1 and Phase 2 receiver work (hardware-validated)

- Automatic dual-band channel hopping (default), scan telemetry and dashboard coverage grid.
- `RID_MAX_FRAME` 2332 (oversize fix); decoded-but-untrackable Remote ID reported as `reception` records.
- DRONK Control lifecycle fix (`--duration` limits a session, not the console) and the detection status panel.
