# Milestone 0.3: Remote ID Aircraft Tracking

Tracking is pure Kotlin and independent of Android, USB serial, UDP, and Meshtastic. Only synthetic semantic fixtures are supplied. No real OUI-SPY capture, firmware parser, radio decoder, or physical-device sensor support is claimed. Milestone 0.4 flight paths and persistent history are not implemented.

## Observation and identity policy

`RemoteIdObservation` represents already-decoded, associated aircraft Basic ID and Location fields, not a new wire format. `RemoteIdNormalizer` extracts a valid textual Basic ID and type into the existing `Detection` model, extended with identifier type and optional motion fields.

Supported types are **SERIAL_NUMBER** and **CAA_REGISTRATION_ID**, limited to 20 printable ASCII characters. IDs are preserved exactly, without case folding or trimming. Missing, blank, surrounding-whitespace, non-ASCII, control-character, and overlength IDs are rejected. This input policy does not certify that a manufacturer serial or registration is authentic. The legacy internal `SYNTHETIC` type preserves compatibility for generic Detection call sites; it is not accepted as a Remote ID type by the normalizer.

Missing/unsupported types are rejected, not guessed from MAC address, operator ID, callsign, or position. Binary UTM UUID and specific-session IDs require a verified canonical representation before support. The inspected Sky Spy JSON prints `basic_id` but provides no verified type/association contract for this application. Actual USB parsing remains deferred until captures and field semantics are available.

`AircraftIdentity.cotUid()` feeds the following UTF-8 string into Java's name-based UUID v3 algorithm, then prefixes the result with `ouitak-rid-`:

```text
org.ouitak.remoteid.simulation.v1 + NUL + TYPE_NAME + NUL + EXACT_IDENTIFIER
```

Real observations use namespace `org.ouitak.remoteid.broadcast.v1`. Position, time, sequence number, and receiving sensor do not affect the key. Simulation is isolated from broadcast observations. These frozen simulation fixtures are regression-tested:

| Type | Identifier | CoT UID |
| --- | --- | --- |
| SERIAL_NUMBER | SIM-DRONE-001 | ouitak-rid-ed6b73d8-87d7-32de-bab1-4203787cc791 |
| SERIAL_NUMBER | SIM-DRONE-002 | ouitak-rid-564e6284-f477-379c-a773-74af831130fa |

Hashing is neither authentication nor anonymization. Identical spoofed identities merge by policy; different/changing identifiers produce separate tracks. Do not infer physical-aircraft continuity from proximity. Different identifier types with identical text remain distinct. The previous 0.2 prototype UID scheme is replaced; old ATAK prototype markers may remain stale according to ATAK preferences.

## Positions and available flight information

Both aircraft coordinates must be present, finite and in geographic bounds. The OpenDroneID unavailable `(0,0)` pair maps to no position. No operator/sensor position, zero coordinate, or inferred position replaces missing aircraft coordinates.

HAE is WGS84 ellipsoid altitude in metres. Remote ID unavailable altitude (-1000 m), speed (255 m/s), direction (361°), non-finite and out-of-range values map to missing. Supported horizontal speed is 0–254.25 m/s. Direction is motion relative to true north, with 360° normalized to 0°; the field labelled heading/course does not claim aircraft yaw. No barometric/AGL-to-HAE conversion is guessed.

A missing-position observation can remain semantically identifiable but never creates or refreshes a precise marker. A prior located track retains its earlier timestamp and expires based on that earlier accepted located observation. Old coordinates are never republished as a fresh observation merely because a report lacks coordinates.

## Live state and expiry

`AircraftTracker` holds one latest located observation per UID, with no historical path. Newer reports update that entry. Equal-time duplicates and older reports are ignored without extending last-seen/stale. Future and already-expired observations are ignored. An active-track cap (default 1024) rejects new identities explicitly while allowing existing identities to update; no silent merging occurs.

Default stale timeout is **60 seconds after the last accepted located observation**, not receipt/send time. `active(now)` prunes expired entries and an injectable clock supports deterministic tests. The core accepts one second to one day; the UI cycles **60 / 120 / 300 / 30 seconds**. Changing the setting recalculates local expiry and affects subsequent events; an event already delivered to ATAK is not retroactively changed. Resend to publish the new stale time. ATAK may retain/fade stale markers; staleness does not guarantee hard deletion.

The active list refreshes while foregrounded and is rechecked on return. Activity/process recreation resets the in-memory session and timeout setting to 60 seconds, but UIDs remain stable. The existing bounded 50-observation UI log remains ephemeral and is not a per-aircraft path/history implementation.

## CoT output

- `type=a-u-A`: unknown affiliation, air dimension; no inferred military role or aircraft subtype.
- `contact callsign`: exact XML-escaped broadcast identifier.
- `time` and `start`: observation time, UTC with three fractional digits. `stale`: that time plus configured timeout.
- `point`: aircraft coordinates and available HAE; explicit unknown HAE/CE/LE values otherwise.
- Standard `<track speed="…" course="…"/>` only when both horizontal speed and true course are available, as required by the public track schema. No fabricated motion values.
- Project-owned `<ouitak version="1" verified="false" identifierType="…" lastSeen="…" …/>` detail includes available `altitudeHaeMeters`, `speedMps`, and `headingDegrees`, including partial flight information. This is an OUI-TAK extension, not a claimed ATAK IPC API.
- Remarks identify the fields as unverified broadcast claims and clearly label simulation. Provenance is `m-s` for simulation and `m-r` for real relayed observations.

## UI and local delivery

**Aircraft** switches between the two fixture identities. **Generate detection** / **Start simulation** update only local state for that identity. **Send Test Detection to ATAK** creates and sends a fresh observation for the selected identity; **Send two aircraft to ATAK** sends a fresh observation for each. Repeating either action updates the same UID rather than adding a new identity.

Aircraft A is near 0°, 0°; B is near 8°, 20°, deliberately separated for the offline world-map demonstration. Each has independent synthetic sequence/position state. All observations are explicitly `SIMULATION`; the fixture identifiers are not actual aircraft serials. Loopback UDP delivery is unchanged from 0.2 and still cannot acknowledge ingestion.

## References and validation

[OpenDroneID field definitions](https://github.com/opendroneid/opendroneid-core-c/blob/master/libopendroneid/opendroneid.h) describe identifier types, Basic ID length, units and unavailable Location values. The [implementation](https://github.com/opendroneid/opendroneid-core-c/blob/master/libopendroneid/opendroneid.c) treats textual IDs differently from binary types. No upstream code is copied or linked.

The [public CoT track schema](https://github.com/deptofdefense/AndroidTacticalAssaultKit-CIV/blob/889eee292c43d3d2eafdd1f2fbf378ad5cd89ecc/takcot/mitre/CoT%20Track%20Schema%20%20(PUBLIC%20RELEASE).xsd) requires speed/course in m/s and degrees true. Existing base-event schema references are in RESEARCH.md. `tools/ValidateCot.java` validates generated XML against separately obtained local base/track schemas, without network or external DTD/schema access.

See VALIDATION.md for the actual two-marker ATAK test and receiver-side before/after coordinates. `tools/verify_atak_rows.py` verifies two ATAK UI rows remain two, with only A's coordinate changing. This receiver evidence is stronger than a packet-send result; it does not add an acknowledgement protocol to UDP.
