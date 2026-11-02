# OUI-TAK Milestone 0.8 — Implementation Phase 1

## Delivered

- Extracted the platform-independent models, strict serial parser, framing, association policy, tracker, CoT generator, transport interfaces, and generic TLS CoT stream into `:shared-core` while preserving package names.
- Added `LiveSerialProcessor` in shared core. Android `MonitoringService` and the Linux CLI now use this same serial-to-tracking-to-CoT pipeline.
- Added `:linux-bridge` and `tools/milestone-0.8/linux_serial_bridge.py`. The Linux adapter uses POSIX read-only TTY access and passes bounded byte chunks to the Kotlin processor. It supports a configurable device path, 115200 8N1, reconnection only after the device node disappears (USB unplug/re-enumeration), connection and parse counters, and graceful shutdown.
- Capture-only is the default. The explicit `--live` mode enables live USB input and authenticated TLS forwarding; it requires protected client credential/trusted CA file paths. Synthetic and live modes are mutually exclusive.
- No duplicate JSON parser, aircraft tracker, or CoT generator was added. Synthetic and live sources remain distinct (`SIMULATION` vs `OUI_SPY_USB`). No values are inferred when absent; the legacy record must include a nonempty `basic_id` and valid aircraft coordinates to create a provisional track. Speed, heading, and broadcast time remain unavailable for the captured status-only sample.

## USB reset investigation and controlled capture

Linux CDC-ACM TTY activation sets DTR and RTS together; deactivation clears them together. The Linux adapter itself does not issue DTR/RTS ioctls or serial writes. It uses `O_RDONLY|O_NOCTTY|O_NONBLOCK` and only applies raw 115200 8N1 termios settings. The ESP32-S3 TRM documents a reset sequence involving clearing DTR/RTS, then asserting RTS while DTR is clear, then clearing RTS. The previous pySerial open changed DTR and RTS separately and remains a plausible cause of the older reset, but its USB control transfers were not captured; the exact historical cause is unconfirmed. References: [Linux CDC-ACM driver](https://github.com/torvalds/linux/blob/master/drivers/usb/class/cdc-acm.c), [Espressif ESP32-S3 TRM, §33.4.2](https://documentation.espressif.com/esp32-s3_technical_reference_manual_en.pdf), and the earlier detailed evidence in [SOURCE-INVESTIGATION.md](../milestone-0.5/SOURCE-INVESTIGATION.md).

A bounded 75-second capture on 2026-10-24 used the implemented reader at `/dev/ttyACM0`, with capture-only mode. It opened the interface, read serial data, and closed at the configured deadline. Post-capture enumeration still showed Espressif `303a:1001` and `/dev/ttyACM0`; no reset banner, kernel disconnect/re-enumeration, or read error was observed. The first version of the reader labeled its orderly deadline shutdown as a USB disconnect; that diagnostic has been corrected so only an actual read/descriptor error is reported as a disconnect. This validates the exact open/read method once on the connected setup, not a universal no-reset guarantee.

The captured serial records were four repetitions of the legacy malformed status line:

```text
{"   [+] Device is active and scanning..."}
```

Each was recognized as `legacy scanning text object` and ignored. Results: 0 accepted detections, 4 ignored lines, 0 rejected lines. No detection object appeared. The sample therefore confirms this installed firmware's status output and parser tolerance, but does not confirm its detection records or real RF reception. The eight source-defined detection fields remain `mac`, `rssi`, `drone_lat`, `drone_long`, `drone_altitude`, `pilot_lat`, `pilot_long`, and `basic_id`; they were not observed in this sample.

The previous firmware log's Wi-Fi cleanup was tied to Unified firmware boot behavior, not a direct CDC data write. The source attempts to clear/default Wi-Fi preferences during boot; the prior log did not prove whether stored non-default values existed or whether every operation succeeded. Wi-Fi is not used in this deployment and was not changed in this milestone.

## Running a bounded capture

Build the CLI without elevated privileges:

```bash
./gradlew :linux-bridge:installDist
```

If the current user can read the device, run:

```bash
bash tools/milestone-0.8/run-linux-bridge.sh --device /dev/ttyACM0 --duration-seconds 75
```

On the current development host the TTY is `root:uucp` mode `0660` and the shell user is not in group `uucp`. The completed capture elevated only the already-built serial reader, not Gradle:

```bash
/usr/bin/python3 tools/milestone-0.8/linux_serial_bridge.py \
  --device /dev/ttyACM0 --duration-seconds 75 \
  --processor "$PWD/linux-bridge/build/install/linux-bridge/bin/linux-bridge"
```

Without `--duration-seconds`, the reader runs until Ctrl-C. It retries after normal CDC reset/disconnect with a bounded delay, discards partial frames at disconnect, and never issues serial commands. Do not add a `pyserial` open that separately lowers DTR/RTS.

## Verification

- `./gradlew testDebugUnitTest :shared-core:test :linux-bridge:test --rerun-tasks`: passed. The shared-core module itself has no separate test sources; existing Android regression tests exercise its parser, tracker, CoT, history, and transport behavior, and Linux bridge tests exercise the shared end-to-end processor.
- `python3 tools/milestone-0.8/test_linux_serial_bridge.py`: 3 pseudo-terminal adapter tests passed, including read-only open flags and raw 115200 8N1 setup.
- `./gradlew :linux-bridge:installDist`: passed.
- `git diff --check`: run after documentation updates.
- Android instrumentation was not run; no Android target was attached. No CoT was sent to TAK Server; no firmware or server settings, certificates, or keys were changed.

## Still requires a broadcasting Remote ID drone

The connected ESP32 without a drone can validate USB open/read/reconnect behavior, boot and status classification, malformed JSON handling, and that no marker is created without a valid Basic ID and aircraft coordinates. Confirming actual detection JSON fields, aircraft ID/location association, RF reception, update cadence, and an end-to-end real observation requires a controlled Remote ID broadcast. Real observations must not be forwarded to TAK Server until a separate authorization is given.

## TAK operation after Phase 1 security remediation

Earlier private integration testing demonstrated synthetic CoT delivery to an operator-controlled TAK deployment. Its destination, credential locations and client metadata are intentionally withheld. Publication examples do not identify that server or claim successful connectivity. The old `--forward-synthetic` runtime option is now rejected: DEMO must never contact TAK.

The current modes are DEMO (synthetic, no TTY/TAK), CAPTURE (physical USB, no forwarding), and LIVE (physical USB with explicit authenticated TAK configuration). See [private configuration and migration](../DRONK-SECURITY-PHASE-1.md). The shared TLS transport retains TLS 1.2+, CA validation, hostname verification and client certificate authentication. A separate network address can be configured without changing the certificate hostname/SNI.

Build the bridge using `./gradlew :linux-bridge:installDist`. For an authorized legacy receiver capture, supply an explicit device path to `linux_serial_bridge.py --processor ... --device ...`; it remains read-only 115200 8N1. For LIVE, supply `--live` and a complete private DRONK configuration. No personal destination or credential defaults remain. The launcher `tools/milestone-0.8/ouitak-live` reads that configuration, validates it before USB discovery, selects the matching ESP32-S3 vendor/product identity, and refuses absent/ambiguous receivers. It does not install an autostart service or change USB permissions. Existing installed launcher copies are not automatically updated by a Git commit: review and reinstall separately when ready.

The existing guarded reconnect policy is retained: a real node disappearance permits waiting for reappearance; a read error while the node remains present stops rather than repeatedly reopening it. Disconnect discards partial lines. Neither reader sends serial payloads or explicit DTR/RTS ioctls. Opening a TTY can still reset the board; no zero-reset guarantee is made. Scanning status never creates an aircraft, and a TLS write is not TAK application receipt.

Logs remain private local files with mode 0600 in a 0700 directory and configurable retention. Host logs avoid credential contents, private destinations and receiver device-path identifiers. The original synthetic fixtures remain labeled; no synthetic data becomes a real RF observation. No hardware device or production TAK Server was contacted during security remediation. Receiver RF operation, field connectivity and application receipt still require separate authorized hardware validation.

## Phase 1.1 audit corrections

Code `1786cc1` corrects the source launcher's log fallback to `${XDG_STATE_HOME:-$HOME/.local/state}/ouitak/logs`, preserving XDG overrides and supporting an explicit `OUITAK_LOG_ROOT`. To inspect it safely without reading configuration or discovering USB, use **the reviewed source wrapper**:

```bash
bash tools/milestone-0.8/ouitak-live --print-log-dir
```

Do not pass this diagnostic to an unreviewed older installed copy: old versions may ignore arguments. Installed copies were not overwritten automatically. Runtime logs and historical literal-tilde directories are Git-ignored; no private logs/directories were deleted.

TAK sends now run in the bounded sender described in [recovery](../DRONK-RECOVERY.md). Startup failures have safe actionable codes; serial ingestion remains responsive during a TAK stall. Pilot forwarding follows the private/public policy described in [security configuration](../DRONK-SECURITY-PHASE-1.md#phase-11-audit-fixes-1786cc1); local decoding is unaffected. DEMO ignores TOML receivers but cannot take explicit devices or forward.
