# OUI-TAK Android prototype

This was the project's top-level README before the DRONK source release. It describes the earlier Android companion app (`app/`), which still builds and shares its core with the DRONK Linux bridge.


Current Linux receiver development is **DRONK — Drone Reconnaissance & Observation Network**. Start the local [DRONK Command Center](../tools/dronk/README.md) with `bash tools/dronk/run-dronk.sh --demo` and open `http://127.0.0.1:8765`, then click **Start DRONK**. Host monitoring defaults to STOPPED; SUSPECTRUM scans independently while powered. See the [operating model](DRONK-OPERATING-MODEL.md). See [C5-0.2 host integration](DRONK-C5-0.2.md) for multi-receiver support and validation limits. Resuming work: [recovery checkpoint](DRONK-RECOVERY.md). First hardware test: [C5 bring-up](C5-HARDWARE-BRINGUP.md). Repository and Android names remain unchanged; the sections below describe the earlier Android prototype.

An open-source, offline Android companion prototype for OUI-SPY RF observations and future ATAK interoperability. Milestone **0.4.0** adds persistent aircraft-specific history, ATAK breadcrumb drawings and offline replay to synthetic Remote ID tracking.

**Local ATAK ingestion was demonstrated** with the official ATAK-CIV 5.5.1.8 SDK developer APK on the API 34 x86_64 emulator, offline. USB parsing, TAK Server, and RAK4631/Meshtastic remain placeholders. USB discovery/permission/attach status is available, but real detections are not decoded. All tracking/history demonstrations use synthetic fixtures. No ATAK binaries or third-party firmware are included.

## Run

Use JDK 17, Android SDK platform 37, and build tools 36.0.0. Android Studio 2026.2.1 can open this directory. Set `ANDROID_HOME` or put `sdk.dir=/your/Android/Sdk` in an untracked `local.properties`.

```sh
./gradlew :app:assembleDebug :app:testDebugUnitTest :app:lintDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell am start -n org.ouitak.app/.MainActivity
```

The wrapper pins Gradle 9.3.1 with a SHA-256 checksum; AGP 9.1.1 provides built-in Kotlin. Minimum Android API 26; compile/target API 37. Initial builds require network access for dependencies and an installed SDK. After dependencies are cached, append `--offline`. Simulation requires no internet, account, sensor, or ATAK installation. Android INTERNET permission is declared for loopback sockets; the implemented destination is fixed to 127.0.0.1. Runtime internet connectivity is not required.

## Simulation

Tap **Generate detection** for one observation or **Start simulation** for one every two seconds. **Stop simulation** pauses generation; **Clear history** clears observations and the XML preview. Simulation stops when the app leaves the foreground. The latest 50 displayed observations are ephemeral. Separate aircraft-specific SQLite history is recorded by default, retained locally for 7 days, with recording, retention and deletion controls.

**Aircraft** switches fixture identities. **Marker expiry** cycles 60/120/300/30 seconds (default 60). **Send two aircraft to ATAK** submits both; **Send Test Detection to ATAK** sends a fresh update for the selected aircraft. The active list holds one latest located observation per typed identity; repeated reports reuse its UID. See [tracking policies](TRACKING.md).

All generated observations are explicitly marked `SIMULATION`, use fictional identifiers `SIM-DRONE-001` and `SIM-DRONE-002`. Aircraft A is near 0° / 0°; B near 8° / 20°, separated for the offline map test. They do not transmit RF Remote ID. The screen previews the latest generated CoT XML. **Send Test Detection to ATAK** generates a fresh synthetic observation and submits one event to 127.0.0.1:4242/UDP. Open ATAK first with its UDP input enabled. UDP offers no acknowledgement, so the app reports socket submission and unverified ingestion; inspect ATAK to confirm the marker. The foreground-only simulator is not automatically sent; **Monitoring controls → Start Monitoring** explicitly starts continuous simulated aircraft/trail delivery that continues with ATAK foregrounded. **Stop Monitoring** or the notification Stop action ends it. See [local ATAK setup and test procedure](LOCAL-ATAK.md).

CoT uses unknown-air type `a-u-A`, simulated provenance `m-s`, stable typed-identifier UIDs, UTC millisecond observation timestamps, a configurable stale interval (60 seconds by default), XML escaping, and explicit unknown-error values. Missing coordinates prevent event generation. See [architecture](ARCHITECTURE.md) and [upstream research](RESEARCH.md).

## History, replay and privacy

Open **History and replay controls**. **Load synthetic flight history** stores eight reports for two aircraft; **Send stored trails to ATAK** publishes their independent drawings and available reported locations. **Trail length** changes the point limit; **Retention** controls stored report age. Live markers, historical drawings and replay have distinct identities. Control-station, takeoff and receiver reports retain their own timestamp/provenance/validity; takeoff never implies a current operator location.

Select **Replay speed**, then **Replay stored history**. Pause, Resume and Reset use the monitoring foreground service. Reset rewinds the same replay session and removes its previous presentation from the tested ATAK build, leaving live identities alone. Completed replay stays idle until Reset/Stop; it holds no wake lock or polling loop while idle. Original timestamps remain in history; replay event timestamps are current. Replayed objects are clearly labeled. See [history/replay policies and schema checks](HISTORY-REPLAY.md) and [verification](VERIFICATION-0.4.md).

Recording can be disabled. **Delete all stored observations** stops monitoring, turns recording OFF and deletes local SQLite history. It does not delete ATAK's copies. There is no cloud sync, analytics or telemetry; backup/transfer is excluded. Storage is app-private but unencrypted. Receiver GPS is opt-in, separate from broadcast control-station/takeoff fields. Physical background/USB/GPS reliability has not been verified. See [service permissions and disabled USB parser](BACKGROUND-MONITORING.md).

## Contribute and license

See [CONTRIBUTING.md](../CONTRIBUTING.md), [roadmap](ROADMAP.md), and [validation](VALIDATION.md). Original project code is Apache-2.0. Dependency review and third-party licensing are recorded in [DEPENDENCIES.md](DEPENDENCIES.md). No firmware or ATAK code is linked or redistributed.

Never include credentials, TAK certificates, real device identifiers, private location histories, or generated APKs/build output in commits.
