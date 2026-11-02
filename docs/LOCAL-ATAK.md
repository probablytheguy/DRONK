# Local ATAK integration — Milestone 0.2

## Mechanism and scope

OUI-TAK sends one legacy CoT XML event as UTF-8 in one UDP datagram to **127.0.0.1:4242**. The destination is fixed to loopback: no LAN, multicast, DNS, TAK Server, or internet endpoint is implemented. Android's `INTERNET` manifest permission is required even for loopback sockets; its name does not mean internet connectivity is required. Sending occurs on a worker thread, once per explicit button press.

ATAK must already be running, initialized, and listening on its enabled UDP 4242 input. OUI-TAK does not launch ATAK, create its network inputs, or silently configure it. The button generates a fresh fictional observation, adds it to the in-memory detection log, and serializes it before submission. Regular simulation updates only local tracking, the detection log and XML preview; they are not automatically transmitted.

Diagnostics distinguish event generation, socket submission/failure, and unverified ingestion. UDP has no application-level acknowledgement. A successful socket call cannot prove that ATAK is installed, listening, accepting the XML, or displaying a marker. OUI-TAK therefore never reports receiver confirmation. Verify the marker in ATAK and inspect its identity/coordinates.

## Primary-source research

Inspected the official [TAK Product Center ATAK-CIV 5.5.1.8 release](https://github.com/TAK-Product-Center/atak-civ/releases/tag/5.5.1.8), source tag commit `7d583c8834f7f432da8dfc4a1e7c61d7df65e846`.

- [CotService.java](https://github.com/TAK-Product-Center/atak-civ/blob/5.5.1.8/atak/ATAK/app/src/main/java/com/atakmap/comms/CotService.java) creates the default UDP input `0.0.0.0:4242:udp` and TCP input on the same port. This is a configurable network CoT interface, not a guaranteed inter-app IPC endpoint. The selected same-device loopback path was demonstrated on the test build.
- [TAK protocol documentation](https://github.com/TAK-Product-Center/atak-civ/blob/5.5.1.8/takproto/README.txt) describes legacy XML UDP, directed TCP, streaming/server framing, and newer negotiated TAK protocol. No server protocol or TLS is needed for this local test.
- [AtakBroadcast.java](https://github.com/TAK-Product-Center/atak-civ/blob/5.5.1.8/atak/ATAK/app/src/main/java/com/atakmap/android/ipc/AtakBroadcast.java) explicitly distinguishes LocalBroadcastManager receivers from exported system receivers. [ImportExportMapComponent.java](https://github.com/TAK-Product-Center/atak-civ/blob/5.5.1.8/atak/ATAK/app/src/main/java/com/atakmap/android/importexport/ImportExportMapComponent.java) registers import actions through its component receiver mechanism. The SDK's `docs/broadcastlist.txt` lists import/CoT actions but listing an action does not establish a public external broadcast contract. No guessed broadcast is used.
- [ATAK AndroidManifest.xml](https://github.com/TAK-Product-Center/atak-civ/blob/5.5.1.8/atak/ATAK/app/src/main/AndroidManifest.xml) exports `ImportFileActivity` with ACTION_SEND file associations, including `.cot`, and ACTION_VIEW content-URI import. A file-import handoff is a user-driven alternative, requiring URI permissions and lifecycle testing; it does not provide marker-ingestion acknowledgement. It is not implemented here because loopback UDP is simpler for live track updates.
- The official SDK includes `main.jar`, Javadoc, and plugin examples/resources. A genuine ATAK plugin could use ATAK's in-process APIs, but would need the intended SDK/build compatibility and plugin loading/signing setup. That would change the companion-app deployment model, so it is not required or implemented.
- [CoordinatedTime.java](https://github.com/TAK-Product-Center/atak-civ/blob/5.5.1.8/takkernel/shared/src/main/java/gov/tak/api/cot/CoordinatedTime.java) uses millisecond-based CoT times. OUI-TAK now serializes UTC timestamps with exactly three fractional digits and retains decimal coordinate notation, escaped callsigns, simulated provenance, and unknown-air classification.

## Emulator finding

**ATAK can install and render on this Android 14 x86_64 emulator.** This conclusion is specific to the official SDK developer APK tested, not a claim about every ATAK distribution or emulator/GPU.

The downloaded SDK archive SHA-256 matched GitHub's asset digest:

```text
c92139d4600f6684fcf9b640fc0fb67a6105940e9bdc2e578ae26f08f477bf56
```

The SDK's `atak.apk` reports package `com.atakmap.app.civ`, version **5.5.1.8 (7f381e4d)**, minimum API 21 and target API 35. Inspection found `armeabi-v7a`, `arm64-v8a`, `x86`, and **x86_64** native libraries. It installed and ran natively on the API 34 emulator. Its first launch required normal EULA/setup, permissions, file access, and background/battery prompts. No OUI-TAK plugin or TAK Server was installed.

With airplane mode **1** and Wi-Fi **0**, pressing OUI-TAK's send button resulted in a yellow map marker in ATAK. Selecting it showed **SIM-DRONE-001**, the expected synthetic position near 0.00101° / 0.00201°, and **Warfighting Symbology > Air track** in its detail panel. This is receiver-side ingestion/rendering evidence beyond a UDP send. Connectivity settings were restored afterward. Screenshots and temporary SDK/APK files stay in /tmp; no ATAK binary is redistributed.

## Reproduce on emulator or physical device

1. Obtain ATAK-CIV from its official distribution or SDK release. For an emulator, inspect `lib/` in the APK for a compatible native ABI; a store-oriented ARM-only APK is not equivalent to the multi-ABI SDK test build. Install ATAK and complete its first-run setup. For a physical device, select its ADB serial explicitly; never copy private data into this repository.
2. Build and install OUI-TAK with JDK 17 and the documented SDK. Open ATAK first and confirm the default UDP input on 4242 is enabled in its network configuration. UI wording varies by release. No TAK Server connection is needed.
3. Use airplane mode and explicitly disable Wi-Fi/mobile data. Confirm both apps can remain running; ATAK may need its background/battery permission setup. Built-in world imagery sufficed for the emulator test; a detailed offline basemap is optional.
4. Open OUI-TAK; the default event stale interval is 60 seconds. Tap **Send Test Detection to ATAK**. Check that generation and socket submission are reported, with receiver confirmation still unverified.
5. Return to ATAK promptly. Find **SIM-DRONE-001** near the equator/prime meridian. At the tested initial world view the marker was visible over the Gulf of Guinea. Select it and inspect its callsign, position, unknown-air classification, and remarks. If needed, use ATAK's object/search tools to locate the callsign and zoom to it.
6. Send again and verify the same UID/marker updates rather than creating another active track. The 0.3 application uses stable typed-identifier UIDs; see TRACKING.md. Record the ATAK build and whether its observed position/time changes. Do not infer physical-aircraft identity from the broadcast claim.
7. Stop sending. The event's `stale` is observation time plus the serializer's configured interval (60 seconds by default, configurable in the 0.3 UI). ATAK may retain or fade stale markers according to its preferences; CoT staleness is not a guaranteed hard deletion. Milestone 0.3 now adds live-track expiration and UI timeout presets; see TRACKING.md.
8. Record receiver-side evidence (synthetic callsign, coordinates, displayed marker/details, tested build and offline settings). A packet capture or OUI-TAK's success message alone is insufficient. Restore connectivity settings after testing.

If no marker appears: confirm ATAK is still running, check its enabled UDP 4242 input, verify current event time/stale against device time, search the expected geographic region, and inspect ATAK logs. An absent/disabled receiver can still yield a successful UDP submission. Do not change destination to a remote address as a workaround for this same-device test.
