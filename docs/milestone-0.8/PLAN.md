# Milestone 0.8 — Real OUI-SPY input: architecture and implementation notes

## Decision

The existing Android path had the needed parser, provisional association, tracker, CoT generator, and authenticated TLS transport split between Android-independent Kotlin core code and Android USB/KeyChain adapters. Milestone 0.8 now puts the shared Kotlin logic in `:shared-core` and adds a Linux bridge adapter. The Milestone 0.6 Java sender remains a transport test tool; it does not process serial input.

**Do not add a second parser, aircraft tracker, or CoT generator.** The smallest safe refactor is a small Kotlin/JVM `:shared-core` module that owns the existing pure core classes while preserving their `org.ouitak.app.core` package names. Move/re-home the parser and pipeline, observation models, association/identity, `AircraftTracker`, `CotGenerator`, transport interfaces, and generic `TakServerTlsTransport` into this module; make `:app` depend on it. Keep the Android USB host, service/UI, and Android KeyChain-backed socket factory in `:app`. Add a Linux CLI adapter which depends on `:shared-core` and passes a Linux JSSE `SSLSocketFactory` to the existing `TakServerTlsTransport`.

This is a source-set/module boundary change, not a parser or CoT rewrite. Existing package names and behavior are preserved. The source moves are listed in the implementation report. Instrumentation still requires a connected Android target.

## Existing data path and reuse

```text
Linux USB byte source
  -> existing OfflineSerialParser + LineFramer
  -> existing SerialIngestionPipeline / LegacyAssociationPolicy
  -> existing Detection
  -> existing AircraftTracker
  -> existing CotGenerator
  -> existing TakServerTlsTransport
  -> TAK Server
```

Verified current code locations:

- `shared-core/src/main/kotlin/org/ouitak/app/core/serial/LineFramer.kt` provides bounded LF framing, CRLF handling, arbitrary chunk boundaries, and discard-through-LF overflow recovery.
- `shared-core/src/main/kotlin/org/ouitak/app/core/serial/OfflineSerialParser.kt` classifies boot/diagnostic/status records, strictly decodes JSON, and validates candidate fields through `CandidateValidator`.
- `shared-core/src/main/kotlin/org/ouitak/app/core/serial/StrictJson.kt` is a bounded-depth strict JSON decoder with duplicate-key and non-finite number rejection.
- `shared-core/src/main/kotlin/org/ouitak/app/core/serial/LegacyAssociationPolicy.kt` exposes the explicit provisional policy and `SerialIngestionPipeline`; the legacy eight-field payload can only become a provisional record when `basic_id` and valid aircraft coordinates are present.
- `shared-core/src/main/kotlin/org/ouitak/app/core/serial/LiveSerialProcessor.kt` is the common transport-independent serial-to-tracker-to-CoT orchestration used by Android and Linux.
- `shared-core/src/main/kotlin/org/ouitak/app/core/{Detection,AircraftIdentity,ReportedLocation,RemoteIdObservation}.kt` define source, identity namespace, role-specific location, and semantic validation behavior.
- `shared-core/src/main/kotlin/org/ouitak/app/core/AircraftTracker.kt` keeps active latest positions, rejects missing positions and non-newer timestamps, and handles stale expiry.
- `shared-core/src/main/kotlin/org/ouitak/app/core/CotGenerator.kt` turns the same `Detection` into synthetic/provisional/real-labelled CoT; provisional event time remains explicitly receiver time.
- `shared-core/src/main/kotlin/org/ouitak/app/core/Interfaces.kt` defines `CotTransport`; `TakServerTlsTransport.kt` is platform-neutral and accepts an injected `SSLSocketFactory`, validates hostnames, and preserves XML-header + LF + CoT framing.
- `app/src/main/java/org/ouitak/app/monitor/Esp32UsbSerialTransport.kt` is Android-only CDC transport, configures 115200 8N1, and deliberately does not toggle DTR/RTS. `AndroidTakTlsSocketFactory.kt` is Android-only because it uses `KeyChain`.
- `app/src/main/java/org/ouitak/app/monitor/MonitoringService.kt` owns Android lifecycle and USB access; its live and debug serial byte feeds now call `LiveSerialProcessor`.
- `app/src/debug/java/org/ouitak/app/monitor/DebugSerialSourceFactory.kt` is the synthetic Android debug source. It exercises the production parser pipeline but is not a Linux serial reader.

## Debug-only Linux bridge

The bridge is implemented in `tools/milestone-0.8/linux_serial_bridge.py` and `linux-bridge/`. Python uses POSIX read-only `open`, raw TTY termios configuration at 115200 8N1, and a bounded internal chunk envelope into the Kotlin processor. It does not call `TIOCMBIS`/`TIOCMBIC`, write payloads, or enable forwarding by default. CDC disconnect closes the descriptor, signals `disconnected()` so partial frames are discarded, and reconnects with bounded backoff only once the device node has disappeared; a read error with the node still present stops the reader rather than reopening. ROM download mode (`waiting for download`) and 60 s of serial silence are logged as errors. `--duration-seconds` supports bounded captures; Ctrl-C is also handled.

The Kotlin process uses `LiveSerialProcessor`, which is also wired into Android `MonitoringService`. Its default mode is capture-only: parsing and CoT generation occur, but no transport is constructed. `--forward-live` is explicit and requires local PKCS#12/password/CA file paths. No forwarding mode was run in this milestone. Raw lines and identity values are not logged; diagnostics report status classification/JSON field names and counters.

## USB reset safety and capture result

Read-only host discovery sees Espressif `303a:1001` as `Espressif USB JTAG/serial debug unit`; an Espressif `/dev/serial/by-id` entry points to `/dev/ttyACM0` (root:uucp, mode 0660). The unique by-id suffix is omitted from project documentation.

The prior capture begins with `rst:0x15 (USB_UART_CHIP_RESET)` and then reports Wi-Fi configuration cleanup. Exact USB control transfers from that earlier capture were not recorded, so its specific reset cause remains unproven. The ESP32-S3 TRM documents the reset sequence as clearing DTR and RTS, setting RTS while DTR remains clear (reset), then clearing RTS to boot. Linux upstream `cdc-acm` activates a TTY by setting DTR and RTS together and deactivates by clearing both together ([driver source](https://github.com/torvalds/linux/blob/master/drivers/usb/class/cdc-acm.c)); Espressif's [ESP32-S3 Technical Reference Manual, §33.4.2](https://documentation.espressif.com/esp32-s3_technical_reference_manual_en.pdf) describes the reset sequence. The previous pySerial procedure additionally set DTR false and then RTS false as separate operations; that is a plausible source of a reset-capable transition, but is not proven to have caused the earlier event.

The implemented reader opens the TTY with `O_RDONLY|O_NOCTTY|O_NONBLOCK`, changes only termios line coding/raw-mode settings, and issues no modem-control ioctl or serial write. A bounded 75-second capture completed. It received four repetitions of the legacy malformed scanning status `{"   [+] Device is active and scanning..."}`; all were classified as ignored status. Counters: accepted 0, ignored 4, rejected 0. No Remote ID detection object was observed. No reset banner or read error appeared in this capture. Post-capture enumeration still showed the same USB VID/PID and TTY; no corresponding kernel disconnect/re-enumeration log was observed. This establishes that this exact read-only procedure worked once without an observed reset; it is not a guarantee about every host/kernel/board combination.

Firmware source inspection says the Wi-Fi cleanup call occurs in the Unified firmware's boot path; the USB TTY itself has no direct NVS erase command. Therefore, if a reset occurs, firmware boot behavior is the mechanism that can run the cleanup. Source shows the operation is attempted; the earlier log does not prove that non-default Wi-Fi settings existed or that all cleanup calls succeeded. Wi-Fi configuration is not used in this deployment and is not a blocker. No firmware/configuration changes were made.

**Requires a broadcasting Remote ID drone:** observing a real detection record; confirming actual field values and emission cadence on this installed firmware; verifying that aircraft Basic ID and aircraft Location are truly associated; observing changing coordinates/altitude; validating over-the-air BLE/Wi-Fi reception; determining if source omission reflects no transmitter or parser/firmware behavior; confirming a real receiver-scoped CoT marker in TAK Aware/iTAK. The previous hardware capture had no actual drone detection and therefore is not evidence for the installed binary's detection records.

## Validation and remaining gates

1. Passed offline: `testDebugUnitTest`, `:linux-bridge:test`, the Python pseudo-terminal adapter tests, and `:linux-bridge:installDist`.
2. Passed shared path: Android live/debug serial feeds and Linux bridge both use `LiveSerialProcessor`; the latter delegates to existing line framing, strict JSON parser, provisional policy, tracker, and CoT generator.
3. Passed device read: bounded capture-only open/read/close; no CoT transport instantiated; no serial writes; no reset or disconnect observed; device remained enumerated.
4. Not tested: live TLS forwarding from actual USB observations (explicitly withheld), Android instrumentation (no Android target attached), real RF detection, real aircraft field association, TAK Aware/iTAK marker delivery, or stale marker behavior.
5. Drone required: prove that this firmware emits actual detection fields under real BLE/Wi-Fi Remote ID traffic, verify actual Basic ID/location association and cadence, and independently display a real receiver-scoped marker in TAK Aware. Keep receive time distinct from broadcast observation time, which this legacy payload omits.
6. No server configuration, certificate, key, firmware, or GitHub state was changed.

## Current review outcome

Architecture outcome: **shared core and Linux bridge implemented; production parsing, association, tracking, CoT generation, and TLS stream transport are reused.** This read-only capture method opened the interface without observable reset evidence and received only the legacy scanning status. No live detection or server transmission occurred. The next gate is a controlled hardware test with a broadcasting Remote ID drone, followed by explicit authorization before any real observation is forwarded to the TAK Server.
