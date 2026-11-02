# Milestone 0.5 emulator serial harness

## What it exercises

The debug variant provides a simulated byte source through `src/debug`. It emits the documented eight-field Sky Spy JSON records as byte chunks and exercises malformed JSON, split/incomplete reads, two aircraft, a repeated sample, a moving aircraft, and a disconnect that discards a partial line. The subsequent sample represents reconnection.

Those bytes enter the same `SerialIngestionPipeline` used by the live USB service. They pass through `LineFramer`, `OfflineSerialParser`, the configured `ProvisionalAssociation`, the service's `AircraftTracker`, `CotGenerator`, and `LoopbackAtakTransport`. Emulated records carry `ObservationSource.SIMULATION`; CoT uses `how="m-s"`, `synthetic="true"`, and synthetic callsigns/remarks. No debug record can be presented as a real USB detection.

The factory in `src/release` returns no simulated source, and the debug UI action is omitted from release. The serial scenario implementation itself exists only under `src/debug`.

## Automated emulator test

With an Android emulator booted and visible to ADB:

```sh
./gradlew testDebugUnitTest assembleDebug assembleDebugAndroidTest
./gradlew connectedDebugAndroidTest
```

`MonitoringServiceTest.testDebugSyntheticSerialRunsThroughParserTrackerCotAndUdpListener` starts the debug serial mode and binds an independent loopback UDP listener on an ephemeral port. It parses received CoT and checks two stable aircraft UIDs, repeated and moved observations, synthetic labeling, `how="m-s"`, valid coordinates, current receiver timestamps, 60-second stale times, malformed-record rejection, and disconnect handling.

## Optional visual ATAK check

Install the debug APK and a compatible ATAK-CIV build on the same emulator. Start ATAK with its UDP input on port 4242, then in OUI-TAK choose **Monitoring controls → Run synthetic Sky Spy serial test (debug only)**. The emulator source defaults to the same local UDP 4242 path as production. Look for **SYNTHETIC PROVISIONAL SYNTH-A** and **SYNTHETIC PROVISIONAL SYNTH-B**. SYNTH-A moves between reports; the label and details identify the data as synthetic.

The automated UDP listener proves receipt of CoT datagrams by the listener, not ATAK rendering. A visual ATAK check is separate receiver-side evidence. The app's current transport is fixed to same-device loopback, so this harness does not forward to a remote TAK Server. A server-side receipt test needs an explicitly configured server endpoint/protocol and independent listener/client evidence.

This harness validates parser-to-CoT behavior in an emulator. It does not exercise Android USB permissions, CDC interfaces, endpoint reads, DTR/RTS behavior, a physical ESP32-S3, or aircraft broadcast timing.
