# OUI-TAK validation

Performed 2026-10-22 (America/Chicago) on the local development host. No physical Android device was used.

## Environment audit

- Linux x86_64 / Omarchy, kernel 7.2.5-3-omarchy.
- Android Studio product metadata: 2026.2.1, build 262.9437.185.2621.16467767.
- `/usr/bin/java`: OpenJDK 17.0.20.1; used for Gradle. Studio's own bundled runtime is separate.
- SDK installed at the locally configured, untracked path: platform 37.0 revision 2, build tools 36.0.0, platform tools/ADB 37.0.1.
- Android SDK command-line tools and system Gradle were absent. No system packages were installed or SDK components added. Gradle was bootstrapped in /tmp, then a standard checked-in wrapper generated.
- ADB reached one running x86_64 emulator; `ro.build.version.sdk` returned **34**. The Oui-Tak-Test AVD configuration points to Android 34 Google APIs x86_64.
- AGP 9.1.1 / built-in Kotlin 2.2.10 / Gradle 9.3.1 / Java 17 compatibility was checked against Google's release documentation and demonstrated by the build. SDK 37 compile/target is independent of the emulator's API 34 runtime.

The execution sandbox initially blocked ADB/Gradle host sockets and network DNS. Those commands were rerun with explicit host-access approval. Gradle caches were placed inside the project under ignored `.gradle-user-home/`.

## Build and unit tests

Succeeded:

```sh
GRADLE_USER_HOME="$PWD/.gradle-user-home" ./gradlew :app:assembleDebug :app:testDebugUnitTest :app:lintDebug --offline
```

Eight tests, zero failures/errors: coordinate and identifier validation, timestamp ordering, synthetic provenance and motion, XML escaping, event attributes and times, stable source-scoped UIDs, missing-position rejection, expiry bounds, altitude, and decimal formatting for tiny coordinates.

Lint: **0 errors, 9 warnings**. Warnings cover a newer available Gradle version, the legacy backup configuration recommendation, and English UI strings in code. The compatible build versions are deliberately pinned; localization remains future work. Initial online build also emitted a non-fatal SDK XML metadata-version warning. The later cached offline build succeeded.

Resolved runtime dependencies: Kotlin stdlib 2.2.10 and JetBrains annotations 13.0. JUnit 4.13.2 and Hamcrest Core 1.3 are test-only. See dependency review.

## Emulator and offline operation

- APK installed successfully with ADB.
- `am start -W` reported `Status: ok` for `org.ouitak.app/.MainActivity` on API 34.
- `tools/emulator_smoke.py` passed: launch, generate one observation, inspect visible synthetic identity/source, parse the displayed CoT event, start repeating simulation, verify multiple observations, stop and verify the count stays fixed, then clear history/XML.
- The smoke test enabled emulator airplane mode, verified it was enabled, and restored its prior setting in `finally`. No physical device or radio was involved. The APK requests no INTERNET permission.

Repeat after installing the APK, using a running emulator:

```sh
python3 tools/emulator_smoke.py --adb /path/to/adb --serial emulator-5554 --cot-output /tmp/ouitak-generated.xml
```

The smoke script requires an emulator serial and uses its screen size and UI bounds rather than device-specific tap coordinates. It resets this app's process/session and temporarily changes airplane mode.

## CoT schema check

The actual XML shown by the app was extracted by the smoke test into /tmp and validated using Java 17 `SchemaFactory` against the public CoT base event XSD from ATAK commit `889eee292c43d3d2eafdd1f2fbf378ad5cd89ecc` (linked in RESEARCH.md). External schema/DTD access was disabled. **Validation passed.** Upstream XSD and temporary checker were not added to the repository. Unit tests independently parse and inspect generated XML without downloading a schema.

## Limits

No blockers remain for the local Milestone 0.1 acceptance checks. API 26 runtime execution has not been tested; Android lint checks the declared minimum. API 34 is the tested emulator. The GitHub Actions workflow is prepared but has not run on GitHub. No release was published and no credentials or binaries were committed.

USB serial input, hardware parser, local ATAK delivery, TAK Server, and Meshtastic are placeholders. No real ATAK ingestion was demonstrated. Hardware captures and a specified ATAK release/input configuration are prerequisites for those later milestones; successful XML/schema checks do not establish integration.


# Milestone 0.2 verification — 2026-10-22

The final application boundary is local ATAK integration. Tracking/history implementation is deferred to roadmap Milestones 0.3 and 0.4. No typed identity registry, aircraft tracking layer, breadcrumb generator, or persistent history was added to the final application.

- Final offline Gradle assemble, unit tests, and lint succeeded: **13 tests, zero failures/errors**, lint **0 errors, 17 warnings** (version/backup recommendations and English UI strings). Existing model validation remains unchanged.
- Six CoT tests cover existing schema refinements plus fixed three-digit UTC milliseconds. Four transport tests verify exact UTF-8 event bytes on loopback, one datagram, honest unverified-ingestion diagnostics, payload/port rejection, propagated socket failures, and socket cleanup. Three detection tests remain.
- Final APK installed and launched on API 34 x86_64. Emulator smoke test exercises simulation generation/start/stop/clear and extracts XML; upstream public base-event XSD validation is performed outside the repository.
- Official ATAK-CIV SDK 5.5.1.8 archive digest matched GitHub's SHA-256. The embedded APK includes x86_64 native libraries, installed, completed first-run setup, and rendered its map. ATAK displayed the explicitly sent SIM-DRONE-001 marker; selecting it showed the expected callsign, synthetic coordinates, and air-track classification. This was done with airplane mode enabled and Wi-Fi disabled, without a server or plugin. See LOCAL-ATAK.md for source references and reproducible emulator/physical-device procedure.
- No application-level acknowledgement is available on this UDP interface. The app correctly reports receiver ingestion as unverified even though receiver-side inspection demonstrated the tested integration.
- ATAK binaries/SDK and screenshots remain outside the repository in /tmp, not redistributed. No GitHub publication occurred.


# Milestone 0.3 verification — 2026-10-22

## Baseline and scope

Milestone 0.2 implementation was reviewed and committed separately as `3218854`, after documentation commit `9a93fca`. The candidate file review found no secret/certificate/private-history patterns or unintended binary/build outputs. The Gradle wrapper JAR is intentional build infrastructure. Local SDK paths, caches, APKs, Python bytecode, and private configuration remain ignored. No ATAK/firmware binary is redistributed.

Milestone 0.3 implements typed identity, normalized semantic input, latest-position live tracking, optional flight metadata, and configurable stale times. No flight paths, trails, per-aircraft historical storage, or persistence (Milestone 0.4) were implemented. The pre-existing 50-observation UI log remains ephemeral.

## Automated checks

The offline Gradle build, JVM tests and Android lint passed with **28 tests, zero failures/errors**. Lint reported **0 errors, 23 warnings** (English UI strings and existing version/backup recommendations). Tests cover fixed typed UIDs, simulation namespace isolation, duplicate prevention, repeated updates, simultaneous aircraft, missing/changing/malformed IDs, unavailable coordinates/motion values, stale boundaries/configuration, future/out-of-order reports, active-state bounds, CoT metadata, and existing transport behavior.

The expanded emulator smoke test passed on API 34 x86_64: default timeout 60 seconds, one identity after repeated observations, two identities after selecting aircraft B, XML preview, foreground simulation, stop, and clearing local state. It restored its prior airplane-mode setting afterward.

Actual XML from the app's default-60-second preview and the sent 300-second inspection event was checked against the separately obtained public CoT base-event and track-detail XSDs. **Both checks passed**, with DTD/external schema resolution disabled:

```sh
java tools/ValidateCot.java /tmp/ouitak-event.xsd /tmp/ouitak-track.xsd /tmp/ouitak03-default.cot /tmp/ouitak03-sent-a-update.cot
```

The public schema links are in RESEARCH.md and TRACKING.md; they are not bundled. The validator runs entirely offline with supplied local schema files.

## ATAK receiver-side integration test

Tested ATAK-CIV SDK developer APK **5.5.1.8 (7f381e4d)** on the existing Android 14/API 34 x86_64 emulator. Airplane mode was enabled and Wi-Fi disabled; there was no TAK Server, MQTT, Meshtastic or internet runtime dependency. The prior connectivity settings were restored afterward.

1. Start ATAK with UDP 4242 enabled. Open OUI-TAK; verify the default timeout is 60 seconds. Select 300 seconds temporarily to leave enough time for inspection; this does not change the default.
2. Tap **Send two aircraft to ATAK**. OUI-TAK reports two active identities and two separate socket submissions, explicitly without receiver acknowledgement.
3. In ATAK, open Overlay Manager → Markers → Markers → Unknown Markers (tested release's hierarchy). The map visibly shows two spatially separate yellow air markers; the list contains exactly one `SIM-DRONE-001` row and one `SIM-DRONE-002` row.
4. Return to OUI-TAK with aircraft A selected and tap **Send Test Detection to ATAK**. Its UID stays `ouitak-rid-ed6b73d8-87d7-32de-bab1-4203787cc791`; its aircraft position advances from `(0.00101, 0.00201)` to `(0.00102, 0.00202)`.
5. Return to the same ATAK list. It still contains exactly two aircraft rows. A's displayed MGRS coordinate changes; B's does not. Both markers remain visible on the map.

| Receiver row | Before A update | After A update |
| --- | --- | --- |
| SIM-DRONE-001 | 31N AA 66245 00111 | 31N AA 66246 00112 |
| SIM-DRONE-002 | 34P CP 89796 84432 | 34P CP 89796 84432 |
| Total aircraft rows | 2 | 2 |

Aircraft B's synthetic position is `(8.00001, 20.00001)` with UID `ouitak-rid-564e6284-f477-379c-a773-74af831130fa`. Its independent sequence is not changed by an A update. The actual sent A update's CoT was 701 UTF-8 bytes, unknown-air type `a-u-A`, simulated provenance `m-s`, HAE 30 m, speed 2 m/s, true course 45°, and explicit unverified claims/last-seen detail.

`tools/verify_atak_rows.py` passed against receiver-side UI dumps from before/after the update, checking the exact two callsigns, a changed A coordinate and an unchanged B coordinate. Screenshots were also visually inspected for actual marker rendering. Temporary dumps/screenshots stay in /tmp and are not committed. This is actual ATAK ingestion evidence, not an inference from a UDP send.

To reproduce the receiver assertion after capturing the two ATAK Unknown Markers screens with `adb shell uiautomator dump`:

```sh
python3 tools/verify_atak_rows.py /tmp/ouitak03-atak-before.xml /tmp/ouitak03-atak-after.xml
```

UI automation is specific to the tested ATAK resource IDs; other builds may require manual inspection or an updated harness. Do not claim integration on an untested build merely because the sender returns successfully. UDP acknowledgement remains unsupported; ATAK's retention/fading of stale markers is controlled by its own settings. Physical sensor parsing, binary/session identifier support and physical-device tests remain outside this milestone. No publication occurred.


## Milestone 0.4 verification investigation

See [verification failures and results](VERIFICATION-0.4.md) for the isolated SQLite/service tests, complete Gradle results, crash cause chain, offline ATAK monitoring evidence, and remaining limitations. This verification pass does not declare Milestone 0.4 complete.


## Milestone 0.4 acceptance — 2026-10-23

Current result: **49 JVM tests**, **14 Android instrumentation tests** (8 SQLite, 3 foreground service, 3 history/ATAK fixtures), debug/test APK build and lint pass. The original 0.3 tests/identity behavior remain covered. Lint has **0 errors, 51 localization warnings**; the legacy backup issue was fixed and the synchronous deletion setting is justified on the storage worker. No dependency upgrade or broad lint baseline was added.

Actual offline ATAK rendered two separate historical polylines. Updating A with a three-point limit changed only its drawing and kept exactly two drawing rows. Replay at 0.5× and 4× was exercised with pause, resume, Reset and finished-state Reset; replay drawings remained separate from original history, and Reset removed only its session. All six control-station/takeoff/receiver labels were observed with correct aircraft suffixes. Tests cover original timestamp preservation, distinct namespaces, malformed observations, ordering, duplicate suppression, per-location retention, deletion and indexed bounded trail queries.

With the final monitoring service active and ATAK foregrounded, two fresh receiver screenshots five seconds apart showed both live aircraft coordinates advancing without duplicate rows, in airplane mode with Wi-Fi disabled. Earlier screen-off/Stop verification remains recorded. This is actual receiver evidence, not an inference from UDP sends. Fourteen exported synthetic events passed base-event/track checks, and all three baseline/update trails passed the full unmodified public Free Form schema.

See [complete results, source links, reproduction commands and limitations](VERIFICATION-0.4.md), [history/replay policy](HISTORY-REPLAY.md), and [foreground service/disabled USB parsing](BACKGROUND-MONITORING.md). Physical Android 14–17, real USB/GPS reliability and large-history replay performance remain unverified. ATAK retains its own archived objects; local cleanup does not erase those copies. No publication occurred.
