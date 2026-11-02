# OUI-TAK architecture

The existing single Android application module contains a pure Kotlin core and a native Activity. Milestone 0.2 added local transport without replacing those boundaries. Milestone 0.3 adds transport-independent identity and live tracking. Milestone 0.4 adds independent per-aircraft persistence, historical drawing presentation and replay, plus a user-started monitoring foreground service.

## Foundation and delivery: Milestones 0.1–0.2

```text
Simulator -> normalized Detection -> CotGenerator -> XML preview
                  ^                       |
Future sensor/parser interface            +-> explicit send -> LocalAtakIntegration
                                                                  |
                                                      LoopbackAtakTransport
                                                        127.0.0.1:4242/UDP
                                                                  |
                                                       configured ATAK input
```

`core/Detection.kt` defines observation and receipt timestamps, an identifier, optional WGS84 aircraft coordinates/HAE, and observation source. Coordinates must be finite and geographically valid. Identifiers are bounded printable ASCII; receipt cannot precede observation. Missing coordinates remain missing. This internal model is not a firmware wire format, and sensor/operator location must not be substituted for unavailable aircraft location. Future adapters must verify framing, field semantics, identifier types, timestamp provenance, and altitude reference against actual output.

`core/Simulator.kt` emits independent fictional observations for `SIM-DRONE-001` near 0° / 0° and `SIM-DRONE-002` near 8° / 20°. It does not transmit Remote ID over RF. `core/CotGenerator.kt` requires coordinates and generates legacy CoT 2.0 XML with type `a-u-A` (unknown affiliation, air dimension), `how=m-s` for simulation, and `m-r` for future relayed USB observations. It escapes identifier text and uses decimal coordinate notation rather than exponent notation. Unknown HAE/CE/LE values are explicit; known altitude means metres above the WGS84 ellipsoid.

`time` and `start` use the observation timestamp in UTC with three fractional digits for ATAK millisecond compatibility. `stale` is observation time plus 60 seconds by default; the serializer accepts a configured interval from one second to one day. Previewing an old observation does not refresh its time. Milestone 0.3 replaces the prototype source-plus-identifier UID with a typed, versioned identity key. The serializer remains independent of live-state management and does not manage trails.

`core/Interfaces.kt` defines independent sensor parsing, OUI-SPY USB input, and CoT transport contracts. `LoopbackAtakTransport` implements local ATAK submission through a fixed IPv4 loopback destination, one UTF-8 XML event per datagram. It rejects empty/oversized payloads, closes sockets, propagates failures, and returns endpoint/byte-count diagnostics. The Activity schedules the socket operation on a worker thread. No remote address, multicast destination, MQTT broker, server, or mesh is needed.

The UI's **Send Test Detection to ATAK** control creates a fresh observation before sending. Normal simulation updates local live-track state and the observation log/XML preview; transmission remains explicit. ATAK must already be initialized with its UDP 4242 input enabled. Diagnostics distinguish generation, socket submission, and receiver confirmation: UDP provides no application acknowledgement, so successful submission is always labelled as unverified ingestion. Actual marker display was separately demonstrated in the tested ATAK-CIV SDK build on the API 34 x86_64 emulator. See [local integration evidence and procedure](LOCAL-ATAK.md).

`MainActivity` retains the existing bounded 50-observation ephemeral display and foreground-only simulator. Its lifetime does not own persistent history or monitoring. `HistoryFeatures` adapts controls to worker-thread storage/presentation actions. Explicit **Start Monitoring** starts `MonitoringService`, which owns a worker thread, independent simulator/tracker and loopback delivery while ATAK is foregrounded. It stores valid reports and updates each aircraft's single drawing. UI-only simulation remains foreground-only; its log's **Clear history** is distinct from persistent **Delete all stored observations**. See [background lifecycle and firmware status](BACKGROUND-MONITORING.md).

The Kotlin core imports no Android APIs. Storage (`storage/SqliteFlightHistory`) and lifecycle/USB/GPS (`monitor/MonitoringService`) are Android adapters. No parser or real USB serial reads are enabled. Internet permission is needed for loopback sockets, but runtime internet is not needed. Backup and device-transfer exclusions cover legacy and modern Android.

## Implemented boundary: Milestone 0.3 — Remote ID Aircraft Tracking

The pure Kotlin `RemoteIdNormalizer`, `AircraftIdentity`, and `AircraftTracker` consume validated semantic observations after normalization and before CoT serialization. They do not parse USB, BLE, Wi-Fi, Meshtastic, or ATAK-specific messages. Supported textual identifier types, synthetic fixtures and policies are documented in TRACKING.md; actual hardware support still requires verified captures.

The responsibility split is:

- **Identity policy:** deterministic, versioned, namespaced keys from aircraft identifier and identifier type; never from position or arrival time. Broadcast IDs are labels and unverified identity claims, not authenticated identities or anonymized data. Simulation uses a separate namespace and explicit provenance.
- **Live-track state:** one active track per identifiable aircraft claim, with latest accepted position and last-seen time. New reports update the existing key. Duplicate/out-of-order reports are ignored; missing identifiers never receive a shared anonymous marker. Changing identifiers must not be correlated without justified evidence.
- **Validation and metadata:** accept only valid aircraft coordinates for precise markers. Carry available altitude, speed, heading/direction and timestamps with documented units/reference frames; omit unknown data rather than invent it. Keep unknown-air classification unless independently justified. Do not infer aircraft location from operator or receiver position.
- **Expiry policy:** configurable, initially 60 seconds after the last accepted observation, with an injectable clock for deterministic tests. Missing positions do not refresh prior points; delayed observations retain their observation-based expiry and expired inputs are ignored. Serialize expiry as CoT stale time; ATAK's own stale-marker retention/fading is not guaranteed hard deletion.
- **Presentation adapters:** serialize live track updates into CoT and pass them to the selected transport. Sensor and transport implementations must not own tracking identity or expiry rules.

The existing normalized model is extended with identifier type and optional speed/direction fields. `AircraftTracker` stores only the latest valid located report per stable UID, ignores duplicate/older/future/expired reports, prunes expired state, and bounds active identities at 1024. Missing identifiers are rejected before tracking; missing positions do not refresh a previous point. The Activity owns this thread-confined state and sends serialized updates through the unchanged transport interface. The default timeout is 60 seconds, configurable in the core and via UI presets. All identity/expiry rules are tested without Android or transport dependencies. See [TRACKING.md](TRACKING.md) for the exact namespace, supported identifiers, sentinel policy, metadata units, collision limitations, and test procedure.

## Milestone 0.4 — Flight Paths, History and Replay

```text
sensor boundary / Simulator -> Detection + independent location reports
                                 |                     |
                     AircraftTracker (latest)     FlightHistory (observed)
                                 |                     |
                          live aircraft CoT      SQLite repository
                                                       |
                                            trailPoints / ReplayEngine
                                                       |
                                 TrailCotGenerator / LocationCotGenerator
                                                       |
                                          transport interfaces -> ATAK
```

`Detection` preserves the 0.3 identity and aircraft fields, adding optional `ReportedLocation` values for aircraft, control station, takeoff and receiver. Each report has its own position, timestamp, provenance and validity. The original aircraft position/timestamp remain authoritative for live tracking; an explicit aircraft report must agree with them. Missing/invalid coordinates never borrow another location's point. Control station and takeoff are unverified reports; takeoff is never an inferred current operator location. Receiver reports distinguish simulated fixtures from opt-in sensor GPS. Future sensor adapters must verify actual field association and semantics.

`FlightHistory` is a pure repository contract. Android's native SQLite implementation stores identifier, source, timestamps, kinematics and every independent location report. It uses a per-aircraft/time index, deterministic observation-content keys, chronological reads, conflict suppression and transactional cleanup. A duplicate re-receipt ignores receipt-time changes; retention-pruned duplicates are also suppressed. Contradictory observations at the same timestamp are ordered by content key, without inventing causal order. Strict decoding fails visibly on malformed timestamps/coordinates. SQLite schema version 1 requires a deliberate future migration rather than destructive upgrades.

Native SQLite avoids another runtime dependency and supplies the required durable transactions. Android instrumentation tests exercise real persistence, ordering, cleanup, deletion and reopen behavior. Storage runs off the main thread; Room is not used. Database and preferences are app-private, backup-excluded and unencrypted. Secure deletion/vacuum removes local logical history, not copies held by ATAK or forensic flash traces. Recording can be disabled independently of live monitoring; retained history remains available until deleted or expired.

`HistoryPolicy` separates retention (default 7 days, configurable 1 hour–30 days) from displayed trail length (default 20, configurable 2–100 points). Each location expires by its own timestamp. A live marker's 60-second default stale interval remains separate. Cleanup is performed on reads/policy changes and periodically during monitoring, without cloud or background jobs.

`TrailCotGenerator` implements the researched ATAK `u-d-f` drawing representation with real observed vertices and one `ouitak-trail-<presentation UID>` per aircraft. Updates replace that drawing; live `a-u-A` markers remain independent. Historical line segments are explicitly visual connections, never inferred position records. `LocationCotGenerator` creates separate labeled control-station, takeoff and receiver spot markers with role-specific UIDs and a parent-aircraft association. Unknown fields produce no marker. See [representation, schemas and source links](HISTORY-REPLAY.md).

`ReplayEngine` orders stored/synthetic reports and uses an injectable monotonic clock for speed, pause, resume and reset. Frames preserve the immutable original and carry a current display timestamp. Session-prefixed replay UIDs isolate all replay aircraft, location markers and trails from live objects. Reset uses the pinned ATAK deletion-task importer for only that replay presentation and rewinds within the same session; no live UID is targeted. Paused, READY and finished replay retain controls in a user-started foreground service without polling or a wake lock. Stop explicitly ends the service. Replay never writes frames back into history.

ATAK can archive imported drawings and retain stale markers. Local retention/deletion does not guarantee removal of ATAK's copies; retired sessions may require removal there. UDP has no ingestion or deletion acknowledgement. Rendering and Reset behavior were separately tested on the named emulator/ATAK build, not inferred from packet submission. All sensor, persistence, clock and transport boundaries remain independent; USB/Meshtastic protocol implementations are not included.

## Retained adapter boundaries and offline operation

OUI-SPY USB input, optional TAK Server forwarding, and future RAK4631/Meshtastic transport interfaces remain available for later implementations. Their lifecycle, permission, framing, authentication, buffering, payload-size and acknowledgement concerns belong inside adapters rather than tracking/history policy. A mesh adapter may require a different verified encoding; arbitrary CoT XML support is not assumed.

Simulation, normalization, CoT generation, local ATAK integration, tracking/history and replay must operate without TAK Server, MQTT, Meshtastic, or internet connectivity. Adding an optional adapter must not make any external service mandatory. Real identifiers, private coordinates/history, credentials, TAK certificates, and generated artifacts must stay out of public source control; ATAK binaries and third-party firmware are not redistributed.

See [ROADMAP.md](ROADMAP.md) for milestone status and acceptance boundaries, and [RESEARCH.md](RESEARCH.md) for upstream source references.
