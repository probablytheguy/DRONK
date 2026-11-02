# Milestone 0.4 verification and acceptance

Initial failure investigation: 2026-10-22. Final acceptance continued on 2026-10-23 using the Android 14/API 34 x86_64 emulator and ATAK-CIV developer build 5.5.1.8. The initial results below are retained for diagnosis; current acceptance results follow. No GitHub publication.

## Failure classification and fixes

- Marker association: select the latest observation whose identifier is `SIM-DRONE-002`, rather than the last item across both aircraft. Chronological/UID tie-break sorting remains unchanged.
- Instrumentation bootstrap: optional framework test libraries were present on the emulator but absent from the test APK manifest. Explicit test-only `uses-library` declarations load the existing runner/base/mock libraries. The original process died before any test class or database initialization. Requested class: `org.ouitak.app.storage.SqliteFlightHistoryTest`; crashing component: `org.ouitak.app.test/android.test.InstrumentationTestRunner`.
- Actual SQLite initialization failure exposed after bootstrap was fixed: `SQLiteException: Queries can be performed using SQLiteDatabase query or rawQuery methods only`, from `SqliteFlightHistory.onConfigure`. `PRAGMA secure_delete=ON` returns a row and cannot use Android `execSQL`. Use `rawQuery`, consume/verify the setting, and remove the redundant invalid PRAGMA from `deleteAll`. No migration was involved (database version 1); Room is not used.
- Lint, independently reproduced with `--stacktrace`: `UnspecifiedRegisterReceiverFlag` for custom USB permission broadcasts. Use the API-26 flags overload, with `RECEIVER_EXPORTED` on API 33+ and zero on older systems. The receiver only rescans authoritative UsbManager state; broadcast payloads cannot enable parsing.
- Gradle host infrastructure, distinct from app/test failures: the original offline connected task could not resolve `com.android.tools.utp:android-test-plugin-host-additional-test-output:32.1.1`. Downloaded the missing build/test dependencies, then successfully reran the complete suite with `--offline`.
- Foreground test harness: package visibility filtered `getLaunchIntentForPackage`, despite ATAK being installed. The integration test explicitly launches the verified emulator build's component. Production package visibility/permissions remain unchanged. Foreground-service permission failure was not the cause of the original crash.

## Original complete fatal exception

Captured with `adb logcat -d -b crash` in `/tmp/ouitak-instrumentation-crash.log`. Installation paths below are redacted; exception frames and the complete cause chain are retained. No test class had started.

```text
--------- beginning of crash
10-22 22:16:48.151 14612 14612 E AndroidRuntime: FATAL EXCEPTION: main
10-22 22:16:48.151 14612 14612 E AndroidRuntime: Process: org.ouitak.app, PID: 14612
10-22 22:16:48.151 14612 14612 E AndroidRuntime: java.lang.RuntimeException: Unable to instantiate instrumentation ComponentInfo{org.ouitak.app.test/android.test.InstrumentationTestRunner}: java.lang.ClassNotFoundException: Didn't find class "android.test.InstrumentationTestRunner" on path: DexPathList[[zip file "<installed-apk>", zip file "<installed-apk>"],nativeLibraryDirectories=[/system/lib64, /system_ext/lib64]]
10-22 22:16:48.151 14612 14612 E AndroidRuntime: 	at android.app.ActivityThread.initInstrumentation(ActivityThread.java:7216)
10-22 22:16:48.151 14612 14612 E AndroidRuntime: 	at android.app.ActivityThread.handleBindApplication(ActivityThread.java:6926)
10-22 22:16:48.151 14612 14612 E AndroidRuntime: 	at android.app.ActivityThread.-$$Nest$mhandleBindApplication(Unknown Source:0)
10-22 22:16:48.151 14612 14612 E AndroidRuntime: 	at android.app.ActivityThread$H.handleMessage(ActivityThread.java:2236)
10-22 22:16:48.151 14612 14612 E AndroidRuntime: 	at android.os.Handler.dispatchMessage(Handler.java:106)
10-22 22:16:48.151 14612 14612 E AndroidRuntime: 	at android.os.Looper.loopOnce(Looper.java:205)
10-22 22:16:48.151 14612 14612 E AndroidRuntime: 	at android.os.Looper.loop(Looper.java:294)
10-22 22:16:48.151 14612 14612 E AndroidRuntime: 	at android.app.ActivityThread.main(ActivityThread.java:8177)
10-22 22:16:48.151 14612 14612 E AndroidRuntime: 	at java.lang.reflect.Method.invoke(Native Method)
10-22 22:16:48.151 14612 14612 E AndroidRuntime: 	at com.android.internal.os.RuntimeInit$MethodAndArgsCaller.run(RuntimeInit.java:552)
10-22 22:16:48.151 14612 14612 E AndroidRuntime: 	at com.android.internal.os.ZygoteInit.main(ZygoteInit.java:971)
10-22 22:16:48.151 14612 14612 E AndroidRuntime: Caused by: java.lang.ClassNotFoundException: Didn't find class "android.test.InstrumentationTestRunner" on path: DexPathList[[zip file "<installed-apk>", zip file "<installed-apk>"],nativeLibraryDirectories=[/system/lib64, /system_ext/lib64]]
10-22 22:16:48.151 14612 14612 E AndroidRuntime: 	at dalvik.system.BaseDexClassLoader.findClass(BaseDexClassLoader.java:259)
10-22 22:16:48.151 14612 14612 E AndroidRuntime: 	at java.lang.ClassLoader.loadClass(ClassLoader.java:379)
10-22 22:16:48.151 14612 14612 E AndroidRuntime: 	at java.lang.ClassLoader.loadClass(ClassLoader.java:312)
10-22 22:16:48.151 14612 14612 E AndroidRuntime: 	at android.app.ActivityThread.initInstrumentation(ActivityThread.java:7212)
10-22 22:16:48.151 14612 14612 E AndroidRuntime: 	... 10 more
```

## Initial verification results (before remaining acceptance)

| Category | Result |
| --- | --- |
| JVM unit tests | 46 passed, zero failures/errors; includes existing 0.3 tests |
| Android instrumentation | 8 passed, zero failures/errors/skips in standard connected Gradle run |
| SQLite persistence | 6 passed independently before service tests; real SQLite reopen, per-aircraft ordering/deduplication, retention, deletion, malformed timestamps and invalid coordinates |
| Foreground service | 2 passed independently: explicit Start/Stop, and continuing after Activity finishes with ATAK foreground |
| Build | `assembleDebug` and test APK assembly pass |
| Lint | `lintDebug` passes: zero errors, 54 warnings; warnings remain unresolved |
| CoT schema | Captured live synthetic event passes existing event and track XSD checks |
| ATAK integration | Actual two-marker rendering and changed coordinates for both aircraft, offline, while OUI-TAK is backgrounded |

Final complete command (stack trace captured in `/tmp/ouitak-complete-verification.log`):

```sh
GRADLE_USER_HOME="$PWD/.gradle-user-home" ./gradlew --offline assembleDebug testDebugUnitTest lintDebug connectedDebugAndroidTest --stacktrace
```

Independent instrumentation commands, after installing the debug application and androidTest APK:

```sh
adb shell am instrument -w -e class org.ouitak.app.storage.SqliteFlightHistoryTest org.ouitak.app.test/android.test.InstrumentationTestRunner
adb shell am instrument -w -e class org.ouitak.app.monitor.MonitoringServiceTest org.ouitak.app.test/android.test.InstrumentationTestRunner
```

Service tests require the installed ATAK build's `com.atakmap.app.civ/com.atakmap.app.ATAKActivityCiv` activity. Run emulator tests sequentially: concurrent Gradle cleanup can uninstall APKs or terminate a separate instrumentation run. An accidentally overlapping verification attempt terminated the redundant standalone run; the subsequent standard connected suite completed all eight tests successfully.

The existing 0.3 UI regression initially failed its final combined assertion because the added controls pushed the cleared XML preview below the viewport. It already confirmed zero observations. The harness now scrolls to the preview and independently checks `No event generated.`; no application clearing behavior was changed. The rerun passed airplane-mode launch, XML generation, stable identity, two aircraft, simulation, stop and clear checks. Captured CoT also passed `tools/ValidateCot.java` against the existing event and track schemas.

## Receiver-side and lifecycle evidence

Started monitoring through the UI and accepted notification permission. Disabled emulator connectivity (airplane mode enabled, Wi-Fi disabled). Opened ATAK; Android reported ATAK as the resumed activity. Visually inspected fresh screenshots `/tmp/ouitak-monitor-atak.png` and `/tmp/ouitak-monitor-atak-after.png`: exactly two unknown-aircraft map symbols and two callsign rows. A changed from `31N AA 66261 00127` to `31N AA 66254 00120`; B from `34P CP 89811 84448` to `34P CP 89805 84441`. No duplicate rows appeared. These are synthetic fixture coordinates.

Continuous receiver updates prevent UI Automator from obtaining an idle ATAK hierarchy. Its failed dump leaves old output in place; that stale output was rejected. Receiver verification uses fresh screenshots, not stale XML or socket counters. UDP still has no receiver acknowledgement.

Turned the screen off: `dumpsys power` reported `mWakefulness=Asleep` and the monitoring partial wake lock; submissions increased 58 → 72. Reopened OUI-TAK and confirmed active monitoring (74 submissions). Explicit Stop left the counter unchanged at 84 across later checks, `monitorRequested=false`, and no running OUI-TAK service. Original airplane/Wi-Fi settings were restored.

Physical-device battery/Doze reliability, USB attach/detach with actual hardware, GPS behavior, Android 15–17 runtime behavior, trails/replay rendering and complete Milestone 0.4 acceptance were unverified by that initial focused pass; the following section records their later verification. USB parsing remains disabled. Existing aircraft UID generation and normalized sorting were not changed by these fixes.


## Final Milestone 0.4 acceptance — 2026-10-23

All original 46 JVM and eight Android tests remain present. Current totals are **49 JVM tests** and **14 Android instrumentation tests**: eight SQLite tests, three foreground-service tests and three history/ATAK acceptance tests. The initial eight Android tests are included in this total, not an additional count.

### Aircraft trails and updates

With airplane mode on and Wi-Fi off, `HistoryAtakAcceptanceTest#testPublishTwoAircraftTrailsAndLocationRoles` published two four-position synthetic trails and their separate aircraft/location markers. ATAK Shapes → Drawing Objects displayed exactly one `SIM HISTORY SIM-DRONE-001` and one `SIM HISTORY SIM-DRONE-002`, with two visibly separate open polylines on its offline globe. Initial cyan/magenta prototypes were changed to schema-compatible opaque white/pale yellow; final rendering was rechecked.

`testUpdateOnlyAircraftAAndLimitTrail` sent a strictly newer A observation at synthetic `(10,-12)` and a three-vertex trailing window. The receiver still listed exactly two trail objects; A changed and B did not. `tools/verify_atak_rows.py --trails BEFORE.xml AFTER.xml` passed against `/tmp/ouitak04-shapes-before.xml` and `/tmp/ouitak04-shapes-after.xml`:

| Trail | Before | After only A moved |
| --- | --- | --- |
| SIM HISTORY SIM-DRONE-001 | 30N UG 88745 38183 | 30N TM 04776 63923 |
| SIM HISTORY SIM-DRONE-002 | 34P FV 62514 65270 | 34P FV 62514 65270 |
| Drawing count | 2 | 2 |

Screenshots `/tmp/ouitak04-trails-before.png` and `/tmp/ouitak04-trails-after.png` were inspected for actual lines, not just row labels. The generated A update has three links (two line segments), replacing the prior four-vertex geometry under the same trail UID. This tests an independent drawing update, not one marker per position.

The indexed SQLite trail query is tested for per-aircraft separation, length limits and time bounds. Retention cleanup is tested with independently aged location fields, late/reversed arrivals, duplicate re-receipts and exact deletion/reopen behavior. A fresh aircraft report cannot retain an expired control-station location. A newly identified pruning edge case is covered: both original and retention-pruned versions remain deduplicated. Invalid coordinates and malformed persisted timestamps are rejected without inventing positions. Local retention and subsequent trail publication are distinct from ATAK's own archive/removal policy.

### Replay, identity isolation and attributed locations

Through the actual UI, cleared only synthetic development history, loaded eight reports/two aircraft, selected 0.5× speed and started replay. Pause retained eight stored observations. Resume produced further observations and two replay trails while ATAK was foregrounded. Paused receiver geometry remained unchanged in two fresh dumps (`/tmp/ouitak04-paused-before.xml`, `/tmp/ouitak04-paused-after.xml`); all four original/replay drawing objects were present. Original timestamps stayed unchanged in SQLite and metadata; replay CoT uses current event/stale times.

Reset removed that session's two replay trails in ATAK, leaving the two original trails unchanged (`/tmp/ouitak04-after-reset.png`). Resumed from the beginning at 4× and observed completion. Final lifecycle behavior was separately rechecked: `FINISHED` and then `READY` after Reset, with eight stored observations and no idle wake lock. Completed/paused/READY replay retains explicit Reset/Stop without polling; Resume schedules work again. Session-prefixed replay aircraft, trail and location UIDs are covered by unit tests and cannot equal live identities. Reset rejects live aircraft and external target UIDs. Retired sessions can remain in ATAK after process death/Stop; their removal is client-controlled.

ATAK Spot Map was scanned in both directions. All six live labels were present: `SIM UNVERIFIED CONTROL STATION`, `SIM UNVERIFIED TAKEOFF`, and `SIM UNVERIFIED RECEIVER`, each suffixed with `SIM-DRONE-001` or `SIM-DRONE-002`. Replay variants were also present separately. One initial fast scan skipped B's control-station row; a slower reverse scan confirmed it (`/tmp/ouitak04-related-markers.json`). This was a UI scan gap, not proof of missing data. Exported XML and unit tests verify unique role/aircraft UIDs, exact parent association, independent provenance/timestamps/validity and coordinates. The shared synthetic receiver point is intentional; it does not become an aircraft/operator position. Takeoff is never treated as the operator's current position.

### Continuous service repeat

A live repeat exposed and fixed an idle-replay → simulation transition: Start changed mode but did not restart an idle scheduler. `MonitoringServiceTest#testPausedReplayCanSwitchToContinuousSimulation` now covers it. Every explicit start schedules the selected mode; no autonomous restart was added. Final review also ensures a new non-GPS mode removes the previous GPS subscription and excludes cached receiver GPS data.

The final APK was run offline with Start Monitoring, five-point trails, and ATAK as the resumed Activity. Android reported `isForeground=true`, type `specialUse`, while local SQLite held independent latest five-position windows. Fresh screenshots taken five seconds apart (`/tmp/ouitak04-fgs-verification-1.png` and `-2.png`) showed actual receiver updates:

| Aircraft | First ATAK coordinate | Five seconds later |
| --- | --- | --- |
| SIM-DRONE-001 | 31N AA 66248 00115 | 31N AA 66252 00118 |
| SIM-DRONE-002 | 34P CP 89799 84436 | 34P CP 89803 84439 |

Exactly two live aircraft rows remained, with stable identities. The service continuously submits each aircraft's same historical drawing UID with a bounded vertex window. Tiny live trails can be visually unresolved at globe zoom; the broad fixture demonstration above establishes actual line rendering and independent geometry updates. Stop was explicit. The earlier screen-off check (58 → 72 submissions while Asleep, stop stable at 84) remains valid for the service foundation. No claim is made about physical Doze, GPS or USB reliability.

### Schemas, build and tests

Fourteen synthetic exported events passed `tools/ValidateCot.java` against the existing public event/track schemas, including aircraft, associated locations, replay aircraft and a replay removal task. All three baseline/update trail events also passed the **unmodified full Free Form XSD with its local includes** using `tools/ValidateDrawing.java`. Schemas were pinned/downloaded into /tmp, not added to the repository. The public schema uses signed-byte color types; conservative valid opaque colors avoid silently patching or relaxing that schema. Spot/removal events use base validation; project-owned detail extensions are not claimed to have an ATAK extension schema.

Final clean command, with output/stack traces in `/tmp/ouitak04-clean-full-suite.log`:

```sh
GRADLE_USER_HOME="$PWD/.gradle-user-home" ./gradlew --offline clean assembleDebug assembleDebugAndroidTest testDebugUnitTest lintDebug connectedDebugAndroidTest --stacktrace
```

Independent SQLite, service and history runs are recorded in `/tmp/ouitak04-sqlite-final.log`, `/tmp/ouitak04-service-final.log` and `/tmp/ouitak04-history-final.log`. Run instrumentation tasks sequentially. For phased receiver inspection, install the application/test APKs and run:

```sh
adb shell am instrument -w -e class org.ouitak.app.storage.HistoryAtakAcceptanceTest#testPublishTwoAircraftTrailsAndLocationRoles org.ouitak.app.test/android.test.InstrumentationTestRunner
# Inspect/capture Drawing Objects before proceeding.
adb shell am instrument -w -e class org.ouitak.app.storage.HistoryAtakAcceptanceTest#testUpdateOnlyAircraftAAndLimitTrail org.ouitak.app.test/android.test.InstrumentationTestRunner
python3 tools/verify_atak_rows.py --trails /tmp/before.xml /tmp/after.xml
```

Fixtures exported by instrumentation are in the debuggable app's `cache/acceptance`, available via `adb exec-out run-as org.ouitak.app ...`; copy only synthetic fixtures to a temporary directory. Build/test output, captures, databases, public schemas, ATAK and firmware binaries are excluded from the commit.

### Lint classification and limitations

The previous 54 warnings were classified without broad suppression or dependency upgrades:

| Original warning | Count | Disposition |
| --- | --- | --- |
| DataExtractionRules | 1 | Fixed: explicit legacy `fullBackupContent=false`, retaining modern backup/transfer exclusions |
| ApplySharedPref | 1 | Intentional worker-thread `commit` before deletion; scoped explanation/suppression and checked return value |
| SetTextI18n | 51 | Retained English prototype UI/localization debt; CoT numeric serialization remains locale-independent |
| AndroidGradlePluginVersion | 1 | Retained notice; compatible Gradle 9.3.1 / AGP 9.1.1 remain pinned |

Final clean offline lint: **zero errors, 51 warnings** (all SetTextI18n). The Gradle-version notice appeared in the earlier incremental report but was not emitted in this clean offline run. No baseline file hides new errors.

Remaining limitations: only the named Android 14 x86_64 ATAK build was runtime-tested; physical Android 14–17 background/GPS/USB behavior is unverified. Actual firmware revision/serial framing is unknown and parsing stays disabled. Replay snapshots load retained observations into memory, so large histories need further performance work. ATAK owns its imported/archive copies; local deletion/retention cannot erase them, and UDP supplies no acknowledgement. No TAK Server, MQTT, mesh, analytics or internet runtime dependency was introduced. No GitHub publication.
