# DRONK recovery checkpoint

Updated 2026-11-01 after the channel 149 parser fix `aa52133`. Earlier: the first physical C5 session and firmware fix `6ee9e41` (2026-10-30), security Phase 1 (`e72b06e`) and Phase 1.1 code fixes (`1786cc1`). This checkpoint distinguishes committed software verification from **unverified hardware behavior**. The documentation commit follows the code commit; inspect `git log` for the current HEAD.

## START HERE SUNDAY

Hardware status: SUSPECTRUM channel 6 reception is **verified** after `6ee9e41` (2026-10-30), and channel 149 reception and frame classification are **verified** after `aa52133` (2026-11-01): 0 malformed. The next hardware step is the Avata test (step 5). Steps 1–4 below are kept as the procedure for reflashing. Full results: [C5 bring-up session results](C5-HARDWARE-BRINGUP.md#session-results-2026-10-30).

Before anything else, run the interruption checks below (`git status`, `git log`). Keep the OUI-SPY ESP32-S3 unplugged: both boards enumerate as `usb-Espressif_USB_JTAG_serial_debug_unit_<MAC>-if00`.

1. **Identify the C5.** Plug in only the C5, then:
   ```bash
   ls -l /dev/serial/by-id/
   read -r -p 'Paste the verified C5 by-id path: ' C5_PORT
   export C5_PORT
   test -e "$C5_PORT"
   ```
   The by-id path is stable across re-enumeration; `/dev/ttyACM*` numbering is not.
2. **Activate ESP-IDF 5.5.4** (the build refuses any other version):
   ```bash
   source ~/esp/esp-idf-v5.5.4/export.sh
   ```
   Put the board into download mode by hand: **hold BOOT, tap RESET, release BOOT**. Automatic reset does not work while SUSPECTRUM is running (esptool reports `Write timeout`). Then confirm the identity:
   ```bash
   python -m esptool --chip esp32c5 -p "$C5_PORT" --before no_reset --after no_reset flash_id
   ```
   Expect `Chip is ESP32-C5 (revision v1.0)` and `Detected flash size: 8MB`, and check the base MAC against your private log. Stop if anything differs.
3. **Build and flash channel 149.** Use a fresh build directory, check the channel, then flash while the board is still in download mode:
   ```bash
   cd ~/Projects/OUI-TAK/firmware/esp32-c5-receiver
   python3 -m unittest discover -s tests -p test_receiver.py
   idf.py -B build-sunday-ch149 -D SDKCONFIG="$PWD/build-sunday-ch149/sdkconfig" \
     -D 'SDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.channel149' build
   grep -E 'CONFIG_OUITAK_(CHANNEL|COUNTRY)=' build-sunday-ch149/sdkconfig   # expect 149 and "US"
   sha256sum build-sunday-ch149/ouitak_c5_receiver.bin
   (cd build-sunday-ch149 && python -m esptool --chip esp32c5 -p "$C5_PORT" -b 460800 \
     --before no_reset --after no_reset --no-stub write_flash @flash_args)
   ```
   Expect `Hash of data verified` three times. Then **tap RESET alone, without BOOT**, to start the app. Without that tap the board stays silent in download mode.
4. **Check 5 GHz RF reception.** Read status with the DRONK console (step 6) or with this read-only reader. It opens the port the way DRONK does and does not change DTR/RTS. Opening with pyserial's default line handling put the board back into download mode on Friday.
```bash
python3 - "$C5_PORT" <<'EOF'
import os, sys, time, termios, select
fd = os.open(sys.argv[1], os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
a = termios.tcgetattr(fd); a[0] = a[1] = a[3] = 0
a[2] = termios.CLOCAL | termios.CREAD | termios.CS8; a[4] = a[5] = termios.B115200
a[6][termios.VMIN] = a[6][termios.VTIME] = 0; termios.tcsetattr(fd, termios.TCSANOW, a)
end = time.time() + 60
while time.time() < end:
    if select.select([fd], [], [], 0.5)[0]: sys.stdout.write(os.read(fd, 4096).decode(errors="replace")); sys.stdout.flush()
EOF
```
The first few lines after opening can be stale records from the board's USB buffer. Check `session_id` and `uptime_us`.
- **Pass for reception:** `"state":"monitoring"`, `"channel":149,"frequency_mhz":5745`, `rf_frames` rising, `rx_reject` all 0, `malformed_frames` 0 or near 0. Passed on 2026-11-01 with `aa52133`.
- **Resolved 2026-11-01:** Friday's channel 149 malformed frames were not a length problem. A temporary raw-frame diagnostic showed VHT compressed beamforming reports (Action No Ack, subtype 14, OFDM `bb_format` 1) whose sequence control carries nonzero fragment bits. The parser ran its fragment check before checking the subtype. `aa52133` classifies non-Beacon/Action subtypes as unrelated first. The report's declared size matched `sig_len - 4` exactly, so the frame length also holds for OFDM.
5. **Test Avata Remote ID** (unblocked: `malformed_frames` on 149 is explained and 0). Follow [bring-up steps 7–8](C5-HARDWARE-BRINGUP.md#7-attempt-real-avata-remote-id-reception), including the DJI requirements listed there (motors running, Goggles location source). Use the unchanged Phase 2 hopping baseline (`66eab97b…`); do not change the scheduler before this test. DJI documents a Wi-Fi Beacon broadcast; FCC ID SS3-QF2W4K22 points to 5.725–5.850 GHz, so expect channels 149–165, but the exact channel is unmeasured. Pass: `candidate_frames`, then `decoded_packs`, then a `reception` or `observation` record (ID and position, when present, must match DJI Fly). Never commit raw captures, serials or real locations.
6. **Check DRONK Control telemetry** (CAPTURE only, no TAK):
   ```bash
   cd ~/Projects/OUI-TAK
   ./gradlew --offline :linux-bridge:installDist
   python3 tools/dronk/command_center.py --mode CAPTURE --device "front=$C5_PORT"
   ```
   Monitoring runs until **Stop DRONK**. Add `--duration 120` only for a timed diagnostic capture; the console stays open afterwards.
   Open `http://127.0.0.1:8765` and press **Start DRONK**. Don't run the raw reader at the same time. On Friday the receiver card connected over USB but showed `awaiting_data` (operator report, not reproduced here). `DronkReceiverHub` sets `awaiting_data` on connect and only leaves it after a record is accepted. Compare its counters with the raw reader output and check host `rejected`.
7. **Forward to TAK last.** Only after steps 3–6 pass with a locally decoded Avata `observation`, and with separate authorization, follow [bring-up step 10](C5-HARDWARE-BRINGUP.md#10-forward-cot-and-confirm-markers-in-tak-aware) (`--mode LIVE --no-forward-pilot-location`). Production TAK application receipt is unverified.

## START HERE AFTER INTERRUPTION

1. **Do not reset, stash, checkout or clean other people's work.** Inspect the working tree before acting:
   ```bash
   cd ~/Projects/OUI-TAK
   git status --short
   git log --oneline -5
   git diff --stat
   ```
2. Confirm the code history includes `9c406fd` (C5 preflight), `e72b06e` (security Phase 1), `1786cc1` (audit fixes) `6ee9e41` (C5 frame-length fix) and `aa52133` (C5 subtype classification fix). Phase 1 is committed and was independently verified; it is no longer work in progress.
3. Run the isolated regression commands in section 7. Tests use temporary configuration and loopback/PTY fixtures, not the operator's private config, receivers or production TAK Server.
4. Do not flash, open a physical receiver TTY, or forward to TAK until the corresponding [hardware bring-up gate](C5-HARDWARE-BRINGUP.md) is explicitly authorized. Steps 1–6 of that guide were run on hardware on 2026-10-30; steps 7–10 have not been.
5. Keep local configuration, credentials, captures, audit reports and scanner output outside Git. Never publish this existing history.

## 1. Branch and code baseline

| Item | Value |
| --- | --- |
| Branch | `master` |
| Latest code baseline | `1786cc1` — audit fixes, bounded TAK sender, pilot forwarding policy and test isolation |
| Security baseline | `e72b06e` — private configuration, DEMO/CAPTURE/LIVE, aliases and sanitized tracked tree |
| Firmware baseline | `aa52133` — non-Beacon/Action subtypes classified unrelated, hardware-verified on channel 149; `6ee9e41` — `sig_len - 4` frame length and `rx_reject` diagnostics, hardware-verified on channel 6; earlier `9c406fd` (application 0.2.1 / `C5-0.2-preflight`) |
| Earlier host commits | `380419c`, `b8f552b`, `ab7a494` |

## 2. Working-tree and private artifacts

The security changes are committed. Unrelated README/firmware-guide edits and older untracked capture notes/tools may still be present; inspect `git status`, preserve them, and do not bundle them into unrelated commits. This recovery guide and the C5 session guide are refreshed in the separate documentation commit after `1786cc1`.

The active private TOML remains outside the checkout. Phase 1 migrated former TAK references without copying credential contents. Phase 1.1 preserved prior pilot forwarding with an explicit local `forward_pilot_location = true` and a protected backup; other private settings are unchanged. New public/example configurations default to false. Do not overwrite an existing config with the public example. See [Phase 1 configuration](DRONK-SECURITY-PHASE-1.md).

## 3. Architecture

```text
SUSPECTRUM (Seeed XIAO ESP32-C5, firmware/esp32-c5-receiver)
  power-on -> fixed-channel passive Wi-Fi management-frame sniffing (NULL mode)
           -> bounded packet queue -> rid_drain slices -> rid_frame.c + vendored OpenDroneID
           -> JSON protocol v1 records -> bounded output queue -> USB Serial/JTAG task
              (host optional; output lossy; no inbound commands)

DRONK host (Linux laptop)
  tools/dronk/command_center.py   localhost-only HTTP/SSE console, starts STOPPED
    Start -> tools/dronk/host_worker.py -> serial_sources.py (read-only TTYs, up to 8)
          -> linux-bridge --dronk (JVM) -> DronkReceiverHub -> AircraftTracker -> CotGenerator
          -> bounded FIFO / deadline -> TakServerTlsTransport (mutual TLS) -> TAK Server -> TAK Aware clients
    Stop  -> closes TTYs, ends JVM/TLS; never commands the receiver

Legacy paths kept working:
  OUI-SPY (ESP32-S3 Sky Spy JSON) -> tools/milestone-0.8 (ouitak-live, linux_serial_bridge.py)
                                   -> LinuxBridgeMain -> same shared-core
  Android app (app/) -> synthetic / local ATAK UDP / TAK TLS
```

`shared-core` is pure Kotlin shared by the Android app and the Linux bridge. The receiver protocol is in `firmware/esp32-c5-receiver/PROTOCOL.md`; the operating model is in `docs/DRONK-OPERATING-MODEL.md`.

## 4. Implemented and tested **[committed, offline only]**

- **C5 firmware:** fixed channels 1–11, 36/40/44/48 and 149/153/157/161/165; Beacon and NAN Remote ID parsing; ODID outer versions 0–2; reserved and future member slots skipped; strict lengths; split diagnostic counters; bounded queues; USB output task; task watchdog with panic/reboot; `reset_reason` and uptime in records. Builds for channels 6 and 149 with ESP-IDF v5.5.4 (recorded in `docs/C5-PREFLIGHT-HARDENING.md`).
- **Host:** multi-receiver hub (up to 8 streams) with session/sequence replay protection, expected-ID pinning, duplicate suppression, legacy OUI-SPY fallback, and CoT generation with an unauthenticated `<dronk>` extension.
- **Console:** DEMO, CAPTURE and LIVE modes, manual Start/Stop through POST plus a CSRF token, receiver cards and counters, logs, JSON/CSV export, 127.0.0.1 binding.
- **Phase 1.1 verification:** 78 Android/core tests, 28 Linux bridge tests, 35 DRONK Python tests, 10 legacy adapter tests and 30 firmware host tests: **181 passed**. Real loopback TLS tests cover stalled-write cancellation and hostname verification. The firmware corpus runs ASan/UBSan. Isolated desktop/iPhone-sized Chromium checks passed; this is not physical Safari validation.

## 5. Security and reliability behavior **[committed code]**

- Private TOML and environment configuration, explicit mode boundaries, certificate hostname separate from connection address, stable internal raw receiver association and public aliases. No plaintext password setting is supported.
- DEMO ignores stored receivers/pins/aliases, but explicit device arguments still reject. CAPTURE cannot forward. LIVE requires complete settings and protected credentials before USB opens.
- Both Linux bridges enqueue outgoing CoT to a **64-entry FIFO**. Overflow evicts the oldest pending event, preserving the order of retained events. A **10-second send deadline** closes the underlying TCP socket without waiting for the send lock. Expired observations and pending shutdown entries are dropped with separate counters. A timed-out event is not replayed and may have been partially delivered; writes are not TAK application acknowledgements. Intake continues while the sender is blocked.
- TAK telemetry exposes `queued`, `queue_drops`, `timeouts`, `stale_drops`, `shutdown_drops`, `attempts`, `written`, `failed` and safe `error_code`. These are host forwarding counters, separate from the receiver's RF/USB counters.
- Linux pilot/takeoff-location forwarding is explicit: `[tak] forward_pilot_location`, `DRONK_TAK_FORWARD_PILOT_LOCATION`, or `--forward-pilot-location` / `--no-forward-pilot-location`. New defaults are off. Local decoding, logs and exports retain independently valid broadcast locations. Existing private opt-in is preserved; Android prototype behavior is unchanged.
- `ouitak-live` defaults to `${XDG_STATE_HOME:-$HOME/.local/state}/ouitak/logs`; `OUITAK_LOG_ROOT` overrides the whole path. `--print-log-dir` is a read-only diagnostic. Runtime `.log` files and a historical literal `~/` tree are Git-ignored. Do not blindly delete a directory named `~`.
- Safe startup codes include `E_CONFIG_INCOMPLETE`, `E_CREDENTIAL_FILE`, `E_CREDENTIAL_PERMS`, `E_CLIENT_CREDENTIAL`, `E_TLS_TRUST`, `E_TLS_TRUST_EXPIRED`, `E_PASSWORD_ENCODING` and pipe framing categories. Runtime forwarding adds `E_TAK_TIMEOUT`, `E_TAK_CONNECTION`, `E_TLS_HOSTNAME` and `E_CLIENT_AUTH`; inspect the code and local configuration rather than dumping private exception details.

## 6. Firmware build commands **[committed]**

These compile only; never append `flash` or `monitor` without hardware authorization. On the delivered board `idf.py ... flash` fails at automatic reset (`Write timeout`); flash with manual download mode and the esptool command in [START HERE SUNDAY](#start-here-sunday).

```bash
cd ~/Projects/OUI-TAK/firmware/esp32-c5-receiver
source ~/esp/esp-idf-v5.5.4/export.sh
# channel 6 (2.4 GHz)
idf.py -B build-friday-ch6 -D SDKCONFIG="$PWD/build-friday-ch6/sdkconfig" build
# channel 149 (5745 MHz), only where regionally permitted
idf.py -B build-friday-ch149 -D SDKCONFIG="$PWD/build-friday-ch149/sdkconfig" \
  -D 'SDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.channel149' build
```

These exact channel 6/149 commands were build-tested during Phase 1.1 (702,448-byte images, before `6ee9e41`); rebuild them in fresh directories for current firmware. Use new build directory names for another clean build; `sdkconfig.defaults` does not override an existing `sdkconfig`. Other channels go through `idf.py menuconfig` → *OUI-TAK C5 receiver*, in a fresh build directory.

## 7. Regression test commands (offline, no hardware) **[committed]**

From the repository root:

```bash
python3 -m unittest discover -s firmware/esp32-c5-receiver/tests -p test_receiver.py -v
./gradlew --offline :linux-bridge:test :linux-bridge:installDist :app:testDebugUnitTest
python3 -m unittest discover -s tools/dronk/tests -v
python3 -m unittest discover -s tools/milestone-0.8 -p test_linux_serial_bridge.py -v
node --check tools/dronk/static/app.js
```

The Gradle command writes to shared `build/` directories. Don't run it while another agent is building.

## 8. DRONK Control launch commands

From the repository root, with the bridge built:

```bash
bash tools/dronk/run-dronk.sh --demo
# Or skip Gradle when already built:
python3 tools/dronk/command_center.py --demo
```

The console starts STOPPED at `http://127.0.0.1:8765`. DEMO never opens devices or TAK, including when the private TOML contains receivers.

After identifying the physical C5, set `C5_PORT` to its verified by-id path, then:

```bash
python3 tools/dronk/command_center.py --mode CAPTURE --device "front=$C5_PORT"
```

Open the local URL and press **Start DRONK** manually. CAPTURE disables TAK even when private TAK settings exist. **Stop** closes host readers/forwarding only; SUSPECTRUM keeps scanning while powered. Opening/closing a TTY may still cause a reset; there is no zero-reset guarantee.

LIVE is the last hardware gate. Use `--mode LIVE` with the receiver and the complete private TOML; retain CA/hostname checks, review the pilot forwarding policy and monitor sender drop/timeout counters. No production handshake has been attempted in Phase 1/1.1. Never restore the old private runtime defaults or use the removed `--forward-synthetic` option.

## 9. Public release status: source snapshot

The private repository's history and author metadata stay private and are never pushed. Public releases are source snapshots: one fresh commit of the tracked tree to `probablytheguy/DRONK`, with no history imported.

Before each snapshot:
- `python3 tools/security/check_public_tree.py --private-values-file <protected list>` reports 0 findings. The list holds real serials, coordinates, addresses, hostnames, certificate names and receiver IDs, and stays outside Git.
- A pattern-based secret scan of the exported tree finds no keys, tokens, certificates, captures or databases. A dedicated scanner (for example gitleaks) has not been run yet and remains a gate for the next release.
- `LICENSE`, `THIRD_PARTY_NOTICES.md` and `SECURITY.md` are current.
- The tests pass on the exported tree.

Private config, backups, captures, databases, build outputs, audit reports and older untracked capture notes stay out.

## 10. Hardware status and unverified assumptions

- **[verified 2026-10-30]** Seeed XIAO ESP32-C5, chip revision v1.0 (esptool), 8 MB flash, ESP-IDF 5.5.4. Firmware flashed successfully (hashes verified). PCB revision and antenna were not recorded in this repository.
- **[verified]** Channel 6 RF reception with `6ee9e41`: 1,074 RF frames in 45.2 s, 0 malformed, 0 queue/oversize drops, 0 callback rejects, 0 Remote ID candidates (expected with no known transmitter), one session with no unexpected resets. 30 firmware host tests passed, including ASan/UBSan.
- **[found and fixed]** The original firmware took the frame length from `dump_len`, which on this board is `sig_len + 4`, so every frame failed the callback check. Taking `sig_len` instead still left four trailing bytes in the frame, and the beacon IE walk marked about 83% of frames malformed. A raw beacon capture showed the MPDU ends at `sig_len - 4`. The four trailing bytes were **not** verified as a valid CRC/FCS: no CRC-32 match was found.
- **[verified 2026-11-01]** Channel 149 (5745 MHz) reception with `aa52133`: 28 RF frames in 61.0 s, 0 malformed, 0 errors, 0 queue/oversize/USB output drops, 0 `rx_reject`, 0 candidates (expected, no Remote ID source), one session with no unexpected resets. 31 firmware host tests passed, including ASan/UBSan.
- **[found and fixed]** On 2026-10-30, 19 of 23 channel 149 frames were parsed as malformed (40 of 45 in the 2026-11-01 diagnostic run). Raw captures were VHT compressed beamforming reports (Action No Ack, subtype 14, OFDM at 12/24 Mbps) with nonzero fragment bits in sequence control. The parser checked fragment bits before the subtype. Their declared report size gave a 615-byte MPDU, exactly `sig_len - 4`, so frame length is confirmed for OFDM as well as 802.11b. No Remote ID beacon was captured on 149, so 5 GHz Remote ID decoding is still unverified.
- **[unverified]** Automatic USB reset for flashing: esptool times out while the app runs, so each flash needs BOOT+RESET and then RESET. The likely cause, unconfirmed, is that the firmware never reads host-to-device USB data.
- **[unverified]** Delivery of NAN action frames to the promiscuous callback.
- **[verified 2026-11-02, reception only]** The original DJI Avata's Remote ID was received and decoded by SUSPECTRUM: 66 Wi-Fi Beacon packs on channel 149 (5745 MHz, the receiver's tuned-channel report), ODID protocol version 2, Basic ID + Location + System, RSSI −60 to −44 dBm, indoors with a phone on the goggles. No independent reference capture. Details: [field results](C5-HARDWARE-BRINGUP.md#field-results-2026-11-02-first-real-remote-id).
- **[unverified]** Valid aircraft coordinates from the Avata. Indoors with no fix, every pack reported `aircraft_location: unavailable` / `no_valid_position`; no track, no CoT.
- **[explained 2026-11-02]** USB output drops accrue while no host reads the port (0 during two active captures); the receiver then delivers stale buffered statuses and one resync newline per drop on connect.
- **[open]** Malformed frames continue at a low rate during active capture (+5 of 1,306 RF frames in 2 min on 2026-11-02, after 0 in the Phase 2 run). Cause unknown.
- **[implemented, not committed]** Phase 1 SQLite history (`docs/DRONK-PHASE1-SQLITE.md`). Real database: `~/.local/state/dronk/dronk.sqlite3`, holding the 2026-11-02 Avata (66 positionless receptions) and the Phase 0/1 hardware captures (no Remote ID).
- **[implemented, not committed]** Phase 2.1–2.3 historical analysis (`docs/DRONK-PHASE2-ANALYSIS.md`). The real database is now schema v2. Imported files live in `~/.local/state/dronk/imports/`.
  - **To undo the migration:**
    1. Stop the console.
    2. Move `dronk.sqlite3`, `-wal` and `-shm` aside.
    3. Copy `dronk.sqlite3.bak-v1-20261102T190307.162971Z` to `dronk.sqlite3` (mode 0600).
    4. Run the Phase 1 code, which refuses a v2 database.
  - **To recreate it from the files:** run `python3 tools/dronk/dronk_db.py rebuild`, which reads `captures/` and `imports/` and keeps the old database. Afterwards, `python3 tools/dronk/dronk_db.py check` must report `"ok": true`.
- **[verified 2026-11-02]** End-to-end: real Avata GPS positions, then LIVE TAK forwarding, with aircraft and operator markers seen in iTAK. **[implemented, not iTAK-verified]** LAST KNOWN and the 2525 symbols. See the [technical journal](DRONK-JOURNAL.md).
- **[was: prepared, not run]** Outdoor GPS and TAK validation: [checklist](DRONK-OUTDOOR-GPS-TAK-CHECKLIST.md). TAK (Part B) needs separate approval. The private config still has `forward_pilot_location = true`; the checklist starts LIVE with `--no-forward-pilot-location`.
- **[note]** The 2026-11-02 frequency is receiver-reported (its tuned channel), not independently measured. Next field test: keep the Phase 0 capture running and record a reference receiver capture of 5.8 GHz.
- **[unverified]** USB re-enumeration on watchdog reboot; antenna and power integrity; absence of unintended RF emission. Observed: a pyserial open that toggles DTR/RTS reset the board into download mode; the DRONK-style read-only open did not reset it.
- **[unverified]** DRONK Control end-to-end telemetry: the receiver connected over USB but showed `awaiting_data` (operator report).
- **[unverified]** Real TAK Aware display of DRONK markers.

## 11. Remaining risks and hardware gates

- Channel 6 reception is verified. Channel 149 reception and frame classification are verified. Still open on the delivered C5: Remote ID decoding on either band (no source yet); NAN delivery; Avata transport/channel and Remote ID decoding; DRONK `awaiting_data`; automatic USB reset for flashing; passive RF behavior; antenna/power integrity; watchdog reset behavior. Production TAK application receipt is unverified. Public GitHub release stays blocked by historical private metadata (section 9).
- A missed hello after a reboot without a transport disconnect can leave a stream bound to its old session. A real reconnect accepts the new session; retired sessions cannot return. Record this hardware behavior rather than weakening replay checks silently.
- Forwarding is bounded and best-effort. Saturation or outages lose messages; the sender does not persist or retry a timed-out event. Successful writes still need TAK Aware/application confirmation.
- Local logs/exports can include pilot and aircraft claims even when outgoing pilot forwarding is disabled. Review them before sharing.
- Legacy launcher and DRONK provisional namespaces differ; keep stream names stable. Fixed-channel firmware cannot find a transmitter on another channel automatically.
- Credential file references and raw expected receiver IDs still travel in the local JVM argv; local OS users may inspect that metadata. Plaintext passwords are not passed there. A private stdin/config channel is a future hardening task.
- CI still needs bridge/Python/firmware coverage and dedicated security scans. Public release remains blocked by section 9.

## 12. Next tasks

1. Follow [START HERE SUNDAY](#start-here-sunday), then [C5-HARDWARE-BRINGUP.md](C5-HARDWARE-BRINGUP.md) in CAPTURE, stopping at each failed gate. LIVE is last and requires separate operator authorization.
2. Record board/antenna, binary hash, channel, counters, USB resets and reference-capture evidence privately. Do not claim RF capabilities from software tests.
3. Extend CI and review third-party redistribution and public-release notices.
4. Only after separate authorization, prepare/review a sanitized publication artifact; do not publish this history.
