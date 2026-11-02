# OUI-TAK development roadmap

## Current status: DRONK / SUSPECTRUM

The milestone sections below cover the Android prototype (0.1–0.4). Later work moved to the Linux DRONK host and the ESP32-C5 SUSPECTRUM receiver:

| Stage | Status | Reference |
| --- | --- | --- |
| 0.5–0.8: shared core, Linux bridge, OUI-SPY serial parser, TAK TLS transport | Committed (`1c71812`…`4005404`) | [milestone docs](milestone-0.8/README.md) |
| C5-0.1: passive fixed-channel receiver firmware | Committed (`4c2345b`); builds only | [firmware](../firmware/esp32-c5-receiver/README.md) |
| C5-0.2: multi-receiver host protocol, Command Center, operating model | Committed (`ab7a494`, `b8f552b`, `380419c`) | [DRONK-C5-0.2](DRONK-C5-0.2.md), [operating model](DRONK-OPERATING-MODEL.md) |
| C5 preflight hardening: ODID v0–v2, split counters, bounded drain | Committed (`9c406fd`); offline tests and builds | [preflight hardening](C5-PREFLIGHT-HARDENING.md) |
| Security remediation Phase 1: private config, modes, aliases | Committed (`e72b06e`); independently verified, 166 tests | [security configuration](DRONK-SECURITY-PHASE-1.md) |
| Phase 1.1: log paths, isolated tests, bounded TAK sender, pilot policy | Code committed (`1786cc1`); 181 tests and channel 6/149 builds | [recovery checkpoint](DRONK-RECOVERY.md) |
| Hardware bring-up: 2.4/5 GHz reception, Avata Remote ID, TAK Aware | In progress: channel 6 reception verified 2026-10-30 after frame-length fix `6ee9e41`; channel 149 reception and frame classification verified 2026-11-01 after `aa52133`. Avata Remote ID, DRONK telemetry and TAK Aware are **unverified** | [C5 bring-up](C5-HARDWARE-BRINGUP.md#session-results-2026-10-30) |

Next milestones: hardware bring-up in CAPTURE, then authorized LIVE validation; extend CI; review redistribution/notices and prepare a separately authorized sanitized publication artifact. TAK delivery is now isolated from serial ingestion. Channel 6 and channel 149 RF reception are hardware-verified; Remote ID decoding on hardware, Avata Remote ID, automatic USB reset and TAK receipt remain unverified. Current state and resume steps are in [DRONK-RECOVERY.md](DRONK-RECOVERY.md).

## Android prototype history

Milestone 0.4 was the Android prototype's final implementation boundary. Milestone 0.2 is closed in commit `3218854`, separately from documentation commit `9a93fca`. Milestone 0.4 implements aircraft-specific history, trails, replay and independently attributed locations; physical sensor parsing remains outside scope. All milestones must support offline operation without a TAK Server, MQTT, or Meshtastic dependency.

## Milestone 0.1: Offline Foundation — completed

- Native Kotlin Android application; minimum API 26, target API 37.
- Synthetic Remote ID detections, connection status, in-memory detection history, and simulation controls.
- Validated normalized detection model and independently testable CoT XML generation.
- Sensor/parser and transport interfaces, public-release documentation, Apache-2.0 baseline review, and build/test workflow.
- APK build, JVM tests, install/launch, and offline simulation verified on an Android 14 x86_64 emulator.

## Milestone 0.2: Local ATAK Integration — implemented and demonstrated

- Research supported CoT network interfaces, exported Android file-import intents, and SDK/plugin options; avoid guessed inter-app broadcasts.
- Refine CoT XML using decimal coordinates, escaped identifiers, UTC millisecond timestamps, unknown-air classification, simulated provenance, and observation-based stale times.
- Deliver a fresh synthetic event through an explicit **Send Test Detection to ATAK** control using same-device loopback UDP.
- Distinguish event generation, socket submission/failure, and receiver confirmation. UDP offers no acknowledgement; submission alone never proves ATAK ingestion.
- Unit-test transport payloads, endpoint restriction, invalid inputs, failures, and socket cleanup.
- Demonstrate an actual marker and inspect its callsign/position in ATAK while airplane mode is enabled and Wi-Fi is disabled.

Local validation used the official ATAK-CIV SDK developer APK **5.5.1.8 (7f381e4d)**, which includes x86_64 libraries, on the API 34 emulator. Its map displayed the synthetic `SIM-DRONE-001` marker. This verifies the tested build/configuration, not every ATAK distribution or emulator. No server, plugin, internet connection, or physical device was needed for that demonstration. See [local ATAK research and reproducible procedure](LOCAL-ATAK.md). Keep this milestone verified before starting later implementation.

## Milestone 0.3: Remote ID Aircraft Tracking — implemented and demonstrated

Stable tracking is implemented for supported textual serial/CAA-registration identifiers using synthetic semantic fixtures. Actual sensor observations and firmware identifier semantics still require verification. See [tracking policies](TRACKING.md).

- Extract a supported aircraft broadcast identifier and identifier type without assuming the firmware exposes fields that have not been verified.
- Generate deterministic, versioned, namespaced CoT UIDs from identifier and identifier type; position/time changes must not change track identity.
- Maintain one active marker per identifiable aircraft claim, updating position reports without creating duplicate markers.
- Display the broadcast ID as the aircraft callsign/label.
- Include altitude, speed, heading/direction, and last-seen timestamp when available, with explicit units and altitude reference. Do not invent missing values.
- Treat identifiers, coordinates, and kinematics as unverified broadcast claims. Retain unknown-air classification unless independent evidence justifies a subtype.
- Handle missing, malformed, or changing identifiers safely: never merge unrelated unidentified reports into one shared anonymous marker or infer identity continuity from proximity alone. Document the policy for identifier changes and collisions.
- Require valid aircraft coordinates for precise markers; do not substitute sensor/operator location or unavailable-coordinate sentinels.
- Make marker expiration configurable, initially **60 seconds after the last accepted observation**. Define behavior for duplicates, delayed reports, missing positions, and already-expired observations.
- Add unit tests for stable UID generation, identifier types, repeated detections, multiple aircraft, duplicate/out-of-order updates, malformed/missing/changing identifiers, coordinate validity, and expiration boundaries/configuration.

Validation: unit tests cover identity continuity, separate aircraft, updates, safe rejection, and expiry. Offline ATAK displayed two fixture aircraft; after updating A, its displayed coordinate changed while B stayed unchanged and the receiver still listed exactly two aircraft rows. Generated events pass base-event and track schema checks. Broadcast identity remains unverified. See [validation evidence](VALIDATION.md).

## Milestone 0.4: Flight Paths, History and Replay — implemented

- App-private native SQLite history per typed aircraft identity, with strict timestamps/coordinates, duplicate suppression, chronological late-arrival handling and tested reopen/deletion behavior.
- Configurable retention (default 7 days) and trail length (default 20 positions), separately from live stale timeout. Cleanup prunes each location by its own observation time.
- Supported ATAK `u-d-f` drawings: one historical trail per aircraft, updates without duplicate drawings, no per-observation permanent markers, no inferred positions.
- Offline replay with adjustable speed, pause/resume/reset, original stored timestamps, current display timestamps and session-namespaced identities isolated from live aircraft.
- Independent aircraft, reported control station, takeoff and receiver locations, each with timestamp/source/validity. Separate associated labels/markers; takeoff never implies the operator's current position. Broadcast reports remain unverified.
- User-started foreground service for ongoing simulation, trail updates and replay while ATAK is foregrounded. Explicit Start/Stop, notification, opt-in receiver GPS and USB discovery/permission/attach diagnostics. Real USB reads/parsing remain disabled.
- Recording OFF, configurable retention and confirmed deletion; no cloud synchronization, analytics, TAK Server, MQTT or Meshtastic requirement.
- Unit and real SQLite/service instrumentation tests, CoT event/track/full-drawing schema checks, and actual offline ATAK two-aircraft/trail and replay rendering checks. See [verification](VERIFICATION-0.4.md) and [history/replay policy](HISTORY-REPLAY.md).

ATAK retains its own archived drawings/received history. Local cleanup does not erase those copies. Replay Reset uses the tested receiver's supported deletion task for that session; retired sessions may need removal in ATAK. Physical USB/GPS/background reliability and Android 15–17 runtime checks remain limitations, not claims of hardware support.

## Future sensor and transport work — retained, unscheduled

- **OUI-SPY USB input:** pin firmware/device versions; verify real output framing, identifiers/types, units, unavailable fields, timestamp policy, and aircraft-location association. Add a versioned parser, USB permission/lifecycle handling, bounded buffering, and malformed-input tests before claiming support. Physical-device verification remains necessary.
- **RAK4631 mesh / optional Meshtastic transport:** retain adapter interfaces; research actual firmware, payload encoding, packet limits, airtime, acknowledgements, and delivery semantics before implementation. Do not assume arbitrary CoT XML fits the mesh.
- **Optional TAK Server forwarding:** keep an independent transport boundary for future TLS, certificates, authentication, and negotiated protocol support. It must never become a requirement for local simulation, tracking, or history.
- Localization, accessibility, lifecycle/session persistence, and binary-release dependency review remain future improvements.

Do not commit credentials, TAK certificates, real device identifiers, private location histories, firmware/ATAK binaries, or generated build artifacts. No dates, hardware compatibility promises, or publication approval are implied by this roadmap.
