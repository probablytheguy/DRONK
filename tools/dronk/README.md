# DRONK Command Center

Local SUSPECTRUM log console for **Drone Reconnaissance & Observation Network**. Python standard library HTTP/SSE supervisor, buildless HTML/CSS/JavaScript, and the existing JVM tracking/CoT core. No database, public hosting, receiver commands or runtime package installation. The map and aircraft register have been removed; tracking and forwarding remain in the shared core.

## Start locally

From the repository root:

```bash
bash tools/dronk/run-dronk.sh --demo
```

Open **http://127.0.0.1:8765** and click **Start DRONK**. The host monitoring service always starts **STOPPED**, including DEMO. **Stop DRONK** closes serial readers and stops the owned JVM/TAK process while leaving this console running. The launcher builds `:linux-bridge:installDist`, then starts the service. Requires the repository's existing Java/Gradle environment and Python 3.11+ (standard-library TOML). To use the already-built processor without invoking Gradle:

```bash
python3 tools/dronk/command_center.py --demo
```

After Start, DEMO runs two synthetic receivers/aircraft through the same versioned Kotlin parser, normalizer, tracker and CoT generator. It opens **no serial devices**, disables forwarding, uses a separate synthetic UID namespace, and labels aircraft/history/RF values as synthetic. One aircraft deliberately has unavailable altitude and both have unavailable operator positions. There is no HTTP control for changing mode or configuring receivers.

Without `--demo` or devices, the dashboard starts in **DISCONNECTED** mode and opens no devices. `--port 8766` changes the localhost port. Monitoring started with **Start DRONK** runs until **Stop DRONK** by default. `--duration 60` makes each started session a timed diagnostic capture: it stops by itself after 60 seconds and reports `Timed capture complete`. The console keeps running either way until Ctrl-C. An unexpected worker or bridge exit is reported as `ERROR`.

## One or two receivers — manual future hardware steps

The following commands are instructions, not hardware tests performed in this milestone. Substitute actual stable device paths; do not guess a C5 path or reuse the connected S3 path. Opening a TTY can reset a receiver. The reader issues no explicit DTR/RTS ioctl, serial payload, input flush or reset command; no zero-reset guarantee exists.

One C5:

```bash
python3 tools/dronk/command_center.py \
  --device front=/dev/serial/by-id/REPLACE_WITH_C5_A
```

Two C5 receivers:

```bash
python3 tools/dronk/command_center.py \
  --device front=/dev/serial/by-id/REPLACE_WITH_C5_A \
  --device rear=/dev/serial/by-id/REPLACE_WITH_C5_B
```

Optional identity pinning: append `--expect-id front=c5-REPLACE_WITH_FACTORY_ID` for each known receiver. Maximum eight distinct streams. Click Start after opening the dashboard; launch alone opens no TTY. Both receivers need separately configured fixed channels/bands; this host does not tune or control them. OUI-SPY serial records are accepted on an unpinned legacy stream, with the existing provisional policy and pilot-coordinate safeguards. See [host integration details](../../docs/DRONK-C5-0.2.md).

States:

- **CAPTURE:** the host processor is receiving recent physical USB records with forwarding disabled.
- **LIVE:** explicit LIVE configuration enables authenticated forwarding; a connected receiver has supplied a recent record (90-second watchdog). It does not certify an active RF receiver or a real aircraft.
- **DEMO:** explicitly selected synthetic-only scenario, no hardware/forwarding.
- **DISCONNECTED:** no recent connected receiver/processor, or browser SSE connection lost. Old aircraft observations may remain until the existing 60-second stale deadline. A frozen browser connection changes the banner, rather than silently presenting it as live.

Receiver cards distinguish USB connection from RF success and show firmware counters, host rejections/duplicates, and transport errors. Unknown counters appear as `—`; they are not manufactured as zeros. Raw RF traffic/candidate counts are never displayed as confirmed aircraft detections. The log/export pipeline retains only the existing normalized location semantics, never substituting operator or receiver coordinates. Operator validity remains separate; type 0 is takeoff, not current pilot location. Log timestamps are host receipt UTC; device uptime is a separate unsynchronized monotonic value, frozen at its last report when disconnected.

## TAK forwarding

Default is capture-only; no network transport is instantiated. To enable the existing authenticated TLS transport intentionally, provide protected credential paths:

```bash
python3 tools/dronk/command_center.py \
  --device front=/dev/serial/by-id/REPLACE_WITH_C5_A \
  --forward-live --tak-host YOUR_TAK_HOST --tak-port 8089 \
  --client-p12 /protected/client.p12 \
  --password-file /protected/password.txt --ca-file /protected/ca.pem
```

**Field status (2026-11-02):**
- **Verified:** LIVE forwarding of a real aircraft to a TAK server over authenticated TLS. A listen-only client confirmed that the server redistributes the aircraft and operator events, and iTAK displayed both markers.
- **Not yet verified in iTAK:** LAST KNOWN and the new symbols, described below.
- **Details:** [technical journal](../../docs/DRONK-JOURNAL.md).

If the TAK server's public name resolves to the site's own public address, connections to it from inside the network fail. Use `--tak-connect-address <server's local address>`: the certificate is still verified against `--tak-host`.

**Markers:**
- **Aircraft:** a standard 2525 type from the broadcast UA type (rotary-wing drone `a-u-A-M-H-Q`, fixed-wing `a-u-A-M-F-Q`, otherwise `a-u-A`).
- **Operator:** when pilot forwarding is on and the broadcast includes it, the operator is a separate `a-u-G` marker with UID `<aircraft-uid>-operator`. A take-off location is a yellow spot with UID `…-takeoff`. Neither position is ever derived from the aircraft's.
- **Callsigns:** the last four characters of the serial. The full serial is in the `ouitak` detail.
- **LAST KNOWN:** after 60 s without a refresh, each marker gets one LAST KNOWN update on the same UID: its last broadcast position, a grey spot labelled `LAST KNOWN`, the real last-seen time, and `stale` 10 minutes later.
  - **Options:** `--last-known-seconds` sets the retention, and `--last-known-style same-type` keeps the live symbol instead of switching to a spot.
  - **Reasons:** `no_receptions`, `receiver_interrupted` (USB disconnect or receiver silence) or `monitoring_stopped`.
  - **Resuming:** new receptions restore live tracking on the same UIDs.
  - **History:** transitions are kept in SQLite (`/api/aircraft/markers`).

Never put a password directly in shell arguments. DEMO cannot be combined with this option. DEMO ignores valid stored receiver settings, so the same private TOML can launch a demo; explicit device arguments still reject. The dashboard reports transport state, delivery attempts, successful TLS stream writes and failures. A successful stream write is **not** acknowledgement from the TAK application. Duplicate/stale/rejected messages do not forward CoT. Start/Stop operates the entire host service with the fixed launch-time forwarding policy; it cannot edit that policy or credentials. No TAK connection or live forwarding was exercised during development.

## Independent receiver and host lifetimes

SUSPECTRUM boots directly into passive fixed-channel monitoring and runs while powered. Neither this console nor a host/TAK connection is required. There are no scan-start, scan-stop, channel-change or RF transmit commands. Stopping DRONK does **not** stop or power down the receiver. Removing USB that supplies its only power does power it off; independently powered behavior must be tested on the delivered board.

The Python console supervises a separate `host_worker.py` process, which owns the read-only 115200 8N1 TTY readers and JVM bridge. Start launches a fresh worker/process group; Stop signals it, closes serial readers, ends forwarding and waits for process exit (bounded forced cleanup if necessary). Unexpected worker exit is an ERROR and is **not** automatically restarted. Normal USB disappearance/reappearance retains the existing guarded reconnect behavior; an open/configuration failure or persistent read error fails that stream without repeated port opens. Restart is manual. No systemd, login task, desktop autostart, or laptop boot service is installed.

Host state is STOPPED / STARTING / RUNNING / STOPPING / ERROR. Receiver USB state is separately CONNECTED / DISCONNECTED / NOT OBSERVED. With the host stopped, the receiver's physical power and RF state are unknown. Last-known selected band/channel, counters, uptime and numeric ESP-IDF reset reason remain visible. Periodic `monitoring` status is configuration/loop telemetry, not proof of RF reception. RF/candidate counts are never confirmed drone detections.

**Aircraft detections** are kept separately from position. A decoded Remote ID with exactly one textual Basic ID of a known type (serial, CAA registration, UTM UUID, session ID) creates or updates one record keyed by (identifier type, identifier), even with no usable coordinates. States: `DETECTED_NO_POSITION` (shown as *Detected, Position Unavailable*), `TRACKING` (latest record had a valid position) and `STALE` (not seen within the tracker stale timeout). A record moves from `DETECTED_NO_POSITION` to `TRACKING` in place, without a second identity. Each record keeps first/last seen (host receipt UTC), observation count, receivers, transport, channel, frequency, last/min/max RSSI, decoded message types, `position_valid`, the reason, and the last valid position with its own timestamp. Receptions with no Basic ID, no textual ID or conflicting IDs never become aircraft. Positionless aircraft are never added to the active track list and never forwarded to TAK; that path is unchanged and still requires a tracked `observation`. Up to 1,024 aircraft are held in memory; they survive Stop/Start but not a console restart.

**Capture files.** Every started session writes a new append-only JSONL capture under `~/.local/state/dronk/captures` (override with `--capture-dir`, `DRONK_CAPTURE_DIR` or `capture_dir` in the private TOML; `--no-capture` disables it; DEMO captures only with an explicit directory). It holds every raw receiver line before validation, the bridge's validated Remote ID records (including positionless `rid_reception`), telemetry, USB events and session-relative diagnostics, between explicit `capture_start` and `capture_end` records. Restarts always create a new file and never overwrite or append to an older one. Monitoring does not start if the capture can't be created; writing suspends (monitoring continues) below 512 MiB free or at 2 GiB. The dashboard shows the file, its size, and this session's USB output drops, malformed frames, queue drops, oversize drops and USB disconnects. Format, provenance keys and recovery: [CAPTURE.md](CAPTURE.md). Captures contain aircraft serials and raw receiver IDs; keep them private.

**Aircraft history (SQLite).** Decoded receptions are also indexed live into `~/.local/state/dronk/dronk.sqlite3` (`--db`, `DRONK_DB`, TOML `db_path`, `--no-db`). The capture files remain the authoritative record: a database failure never stops capture, and the console re-imports any capture the live index didn't fully cover, at start and after each session. After a console restart, previously seen aircraft come back as `STALE`. The **Aircraft history** panel searches by Remote ID and shows first/last seen, reception counts, sightings (windows with no silence over 300 s, not flights) and a timeline with source file and record number for each reception. CLI: `python3 tools/dronk/dronk_db.py import|rebuild|search|show|status`. Design and checks: [docs/DRONK-PHASE1-SQLITE.md](../../docs/DRONK-PHASE1-SQLITE.md).

**Historical analysis (Phase 2.1–2.3, API only; no UI yet).**
- **Imports:** imported files are first copied into the private `~/.local/state/dronk/imports/`, verified and read-only, so `rebuild` can reproduce them. Limits: 2 GiB per file, 50 per batch, free space checked before copying.
- **Quality:** the database also keeps capture-quality evidence (USB events, receiver lines, diagnostics) and derives receiver coverage, known losses and per-sighting quality: packet gaps, open start/end, coverage gaps, losses. Every derived value records its algorithm version and thresholds.
- **Query routes:** each takes `type`, `id` and optionally `tz` (an IANA zone; default `America/Chicago`, storage always UTC):
  - `/api/aircraft/summary`
  - `/api/aircraft/sightings`
  - `/api/aircraft/appearances?period=day|week|month`
  - `/api/aircraft/receptions`
  - `/api/aircraft/track`
  - `/api/provenance?reception=`
  - `/api/coverage?from=&to=`
- **Responses:** they separate `measured` from `derived`. Tracks contain broadcast aircraft positions only; operator and take-off positions are withheld from tracks and provenance.
- **Configuration:** the optional TOML table `[analysis]` takes `time_zone`, `sighting_gap_seconds`, `packet_gap_seconds`, `edge_seconds` and `status_silence_seconds`. `--time-zone` overrides the zone.
- **CLI:** `dronk_db.py backfill|check|fingerprint` as well.
- **Design and validation:** [docs/DRONK-PHASE2-ANALYSIS.md](../../docs/DRONK-PHASE2-ANALYSIS.md).

Exports: `/api/export?format=csv|json` returns the aircraft detections (one row per aircraft). `&table=history` returns the per-observation track history in the previous format. The bounded log and history rings hold 1,000 entries each in memory; the browser shows the latest 150 logs. History export keeps original independently valid locations and raw timestamps; unavailable fields remain null/blank, never 0,0. CSV text is protected against spreadsheet formula injection. Duplicate observations retain reception metadata without refreshing tracks. No persistent database, credentials, private keys, or CoT XML are exposed. Restarting the console clears the in-memory history and detections; stopping/restarting monitoring retains them. The capture files are the durable record; the SQLite store restores aircraft (as `STALE`) and history after a restart.

## Local host controls

Only `POST /api/control` with exact JSON `{"action":"start"}` or `{"action":"stop"}` changes state. It requires a matching local Host, exact same Origin, `application/json`, a body of at most 256 bytes, and a per-console random `X-DRONK-CSRF` token fetched through `/api/control-token`. GET never changes state. There is no endpoint to choose device paths, change mode, tune RF, flash firmware or alter TAK settings. Browser loss disables controls and marks the displayed connection disconnected; it does not silently stop the worker. These controls protect against cross-origin browser requests; they are not authentication against another local OS user.

## Secure LAN / iPhone access

The server **only binds `127.0.0.1`**, with no remote bind option. Do not expose port 8765 through a router, public tunnel, or unauthenticated proxy. Host/Origin checks prevent casual cross-origin reads/DNS rebinding; they are not user authentication.

For another trusted computer, use an authenticated SSH tunnel from that computer:

```bash
ssh -N -L 8765:127.0.0.1:8765 operator@YOUR_LINUX_HOST
```

Then open `http://127.0.0.1:8765` on the client. For iPhone, use an SSH client supporting local port forwarding: authenticate to the Linux host, forward local `127.0.0.1:8765` to host `127.0.0.1:8765`, keep the tunnel active, then open that local URL in Safari. No server-side LAN bind is needed. iOS backgrounding can pause the SSH tunnel; the dashboard must show DISCONNECTED and resynchronize on return.

If a managed LAN needs direct browser access, put an authenticated HTTPS reverse proxy in front of loopback, enforce the external same-origin policy there, disable buffering for `/events`, and rewrite upstream Host/Origin consistently with the loopback checks. Use a firewall/VPN ACL and a trusted certificate. This deployment is intentionally not provisioned or enabled by this task; SSH forwarding is the documented default. Receiver controls remain read-only regardless of access method; host Start/Stop requires the same-origin token checks through any proxy.

## Tests

```bash
./gradlew --offline :linux-bridge:test :app:testDebugUnitTest :linux-bridge:installDist
python3 -m unittest discover -s tools/dronk/tests -v
python3 -m unittest discover -s tools/milestone-0.8 -p 'test_linux_serial_bridge.py' -v
node --check tools/dronk/static/app.js
```

Socket/PTY tests need a host permitting localhost sockets; they use no physical USB. Build the JVM distribution before the end-to-end PTY test. Optional browser QA with Playwright installed in a separate test environment and existing `/usr/bin/chromium`:

```bash
python tools/dronk/tests/browser_check.py http://127.0.0.1:8765
```

Run that against an explicitly launched DEMO service. It tests actual demo host Start/Stop, logs, safe untrusted text and Chromium desktop/iPhone-sized layouts, not a physical iPhone. Screenshots are written under `/tmp`, not the repository. Runtime dependencies remain Python standard library plus the existing JVM distribution.

Verification for this change is recorded in [the operating model](../../docs/DRONK-OPERATING-MODEL.md). No real RF reception, physical C5 reconnect, mobile Safari hardware test, or TAK application delivery is claimed. The [hardware bring-up checklist](../../firmware/esp32-c5-receiver/HARDWARE-BRINGUP.md) remains applicable.

## Private configuration and public receiver aliases

Read [Phase 1 security configuration](../../docs/DRONK-SECURITY-PHASE-1.md) before LIVE use. `config/dronk.example.toml` contains placeholders only; copy it outside the repository to `~/.config/dronk/config.toml` and use mode 0600. `--config` / `DRONK_CONFIG` can select another private file outside Git. Existing private TAK values were migrated locally, not embedded in the public example. No public example is a tested endpoint.

Use `--mode DEMO`, `--mode CAPTURE` or `--mode LIVE`, with explicit CLI taking precedence over environment and TOML. Existing `--demo` and `--forward-live` remain explicit aliases for DEMO and LIVE; combining them or requesting forwarding with explicit CAPTURE rejects before a worker starts. Live forwarding requires an explicit certificate hostname and client PKCS#12/password-file/CA paths. `--tak-connect-address` optionally routes to another address while retaining certificate hostname verification. Password values are never read from environment overrides.

`[receiver_aliases]` maps each configured stream to its public label; `--receiver-alias front=north` overrides it. The default public label is the stream name. Raw USB receiver IDs remain internal for expected-ID binding, association, collisions and replay/session checks. CoT, logs, receiver telemetry and detection export use public labels. Changing aliases does not change typed aircraft identities or duplicate suppression. Use stable stream names for legacy provisional IDs; leave `legacy_namespace` unset to retain the prior internal namespace. Logs/exports still contain unverified aircraft broadcast claims and should be reviewed before sharing.

## Phase 1.1 reliability and pilot forwarding

The two Linux bridge paths isolate outgoing TAK writes in a 64-entry FIFO with a 10-second deadline. The sender drops oldest pending entries on overflow, stale entries before sending and pending entries at shutdown; retained events stay ordered. Timeouts close the underlying socket and do not replay the uncertain write. The TAK card/logs expose pending depth, queue drops and timeouts; telemetry also includes stale/shutdown drops and a safe error code. RF receiver queue drops are a different counter. A stream write still does not acknowledge TAK application delivery.

Pilot/takeoff-location forwarding is opt-in for new configurations: `[tak] forward_pilot_location = false` is the example/default. Override with `DRONK_TAK_FORWARD_PILOT_LOCATION` or `--forward-pilot-location` / `--no-forward-pilot-location`. The active private configuration was explicitly opted in to preserve previous behavior; review it before LIVE. This policy filters only outgoing Linux CoT. Local decoding, logs and JSON exports retain valid broadcast pilot data, and type-0 takeoff is never relabeled as current pilot position.

Use [the current recovery guide](../../docs/DRONK-RECOVERY.md) and [hardware procedure](../../docs/C5-HARDWARE-BRINGUP.md); Phase 1 is committed. Tests use temporary HOME/XDG configuration and scrub live environment settings. Hardware and production TAK were not accessed by the regression work.
