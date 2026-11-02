# Upstream research — 2026-10-22

## OUI-SPY Unified Blue

Primary repository: [colonelpanichacks/oui-spy-unified-blue](https://github.com/colonelpanichacks/oui-spy-unified-blue). Inspected commit **6e5e2829540925ff1ab5fa6d0d365378818ed56e** using GitHub's tree API and raw source; research files remain in /tmp, not distributed here.

- [Sky Spy wrapper](https://github.com/colonelpanichacks/oui-spy-unified-blue/blob/6e5e2829540925ff1ab5fa6d0d365378818ed56e/src/mode_skyspy.cpp) selects passive WiFi/BLE Remote ID capture without an AP and wraps the raw implementation.
- [Sky Spy implementation](https://github.com/colonelpanichacks/oui-spy-unified-blue/blob/6e5e2829540925ff1ab5fa6d0d365378818ed56e/src/raw/skyspy.cpp): `initializeSerial()` calls `Serial.begin(115200)`. `send_json_fast()` prints JSON with fields `mac`, `rssi`, `drone_lat`, `drone_long`, `drone_altitude`, `pilot_lat`, `pilot_long`, `basic_id`. Status JSON and human-readable diagnostics share serial output. Comments state mesh functionality was removed.
- The variable `altitude_msl` is populated from OpenDroneID `Location.AltitudeGeo`; names alone cannot establish the datum. JSON uses a fixed 256-byte formatting buffer and `%s` insertion for the basic identifier. Framing, truncation, escaping, unavailable values, and multiple firmware versions need testing before trusting input.
- This inspection establishes candidate inputs, not a stable version-independent protocol or verified USB Android compatibility. No parser or serial driver is implemented. The simulator's Kotlin data model is entirely internal.
- Upstream root tree had no standalone LICENSE file in the inspected revision. No source or firmware has been copied into the app; linking/copying any upstream implementation requires a separate license review.

## ATAK and CoT

Inspected ATAK-CIV commit **889eee292c43d3d2eafdd1f2fbf378ad5cd89ecc**. This archived source documents historical behavior; it does not prove that any installed current ATAK version has the same defaults.

- [TAK protocol documentation](https://github.com/deptofdefense/AndroidTacticalAssaultKit-CIV/blob/889eee292c43d3d2eafdd1f2fbf378ad5cd89ecc/takproto/README.md): traditional protocol transports CoT XML, one event per UDP datagram; TCP server streams have specified XML framing. TAK protocol v1 adds protobuf encoding and negotiation. Server authentication may be required before protocol negotiation.
- [CotService](https://github.com/deptofdefense/AndroidTacticalAssaultKit-CIV/blob/889eee292c43d3d2eafdd1f2fbf378ad5cd89ecc/atak/ATAK/app/src/main/java/com/atakmap/comms/CotService.java): `_addDefaultInputs()` creates UDP and TCP 4242 plus multicast inputs. This supports investigating a configured local network input; it is not a guaranteed always-open inter-app API. No hardcoded port or transport is implemented here.
- [CoT type hierarchy](https://github.com/deptofdefense/AndroidTacticalAssaultKit-CIV/blob/889eee292c43d3d2eafdd1f2fbf378ad5cd89ecc/takcot/mitre/types.txt) documents atom `a`, unknown affiliation `u`, and air dimension `A`. We conservatively generate `a-u-A` rather than inferring a military or fixed-wing subtype.
- [Public base event schema](https://github.com/deptofdefense/AndroidTacticalAssaultKit-CIV/blob/889eee292c43d3d2eafdd1f2fbf378ad5cd89ecc/takcot/mitre/CoT%20Base-Event%20Schema%20%20(PUBLIC%20RELEASE).xsd) specifies event timestamps, point values, detail extension structure, and provenance codes including `m-s` simulated and `m-r` relayed. XML generation is tested independently of delivery. Schema validation is not evidence of ATAK ingestion.

No ATAK Android broadcast intent/API is assumed. A companion can investigate configured CoT network inputs; a genuine ATAK plugin would require the appropriate official SDK and release compatibility. TAK Server forwarding additionally needs endpoint, TLS, credentials, framing, and negotiation decisions. Meshtastic is a future constrained transport, not assumed to accept arbitrary CoT XML.

## Android compatibility

[AGP 9.1.1 release notes](https://developer.android.com/build/releases/agp-9-1-0-release-notes) document API 37 support, Gradle 9.3.1, SDK build tools 36.0.0, and JDK 17. [AGP built-in Kotlin documentation](https://developer.android.com/build/migrate-to-built-in-kotlin) explains why there is no separate Kotlin Android plugin. API 26 supports the java.time APIs used here and preserves the requested baseline. Compile/target 37 matches the installed platform and supported AGP. The app still runs on the API 34 test emulator.
