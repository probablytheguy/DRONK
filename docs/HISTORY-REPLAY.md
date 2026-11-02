# Flight history, trails and replay

Milestone 0.4 uses synthetic observations. No OUI-SPY serial parser is enabled.

## Store and locations

Each typed aircraft identity has its own timestamped SQLite history. Identifier, observation/receipt times, aircraft altitude in metres HAE, speed in m/s, true course, and source are preserved when available. Aircraft, reported control station, reported takeoff, and sensor receiver locations have independent timestamps, provenance and validity. Invalid/unavailable coordinates are represented without a position. Takeoff never implies the operator's current location. Receiver GPS is opt-in; no unrelated personal profile data is collected.

A chronological index and deterministic content key handle late arrivals and duplicate receipts. Different observations at the same timestamp have a deterministic content-key tie break; sorting does not guess which contradictory report is true. Strict model validation and timestamp parsing reject malformed persisted observations. A corrupted row causes a visible history diagnostic rather than a fabricated fix.

Native `SQLiteOpenHelper` is used instead of Room: this small append/query/cleanup repository needs no new runtime dependency, supports API 26, and is exercised against actual Android SQLite. Schema version 1 refuses destructive upgrades; future versions need an explicit migration. SQLite JSON is an internal persistence representation, **not** a proposed sensor protocol. Queries/transactions run on storage workers. Data is app-private, unencrypted SQLite, with Android backup/transfer disabled. There is no cloud synchronization, analytics or raw-identifier logging.

Default retention is 7 days; UI options are 1 hour, 1 day, 7 days and 30 days. Core policy accepts 1 hour–30 days. Cleanup runs on history access, policy changes and during monitoring. Each location ages by its own timestamp; a fresh aircraft fix does not retain an old control-station report. **History recording OFF** stops new persistent writes; it does not delete previously stored data or stop live markers. **Delete all stored observations** confirms deletion, stops monitoring/replay, persists recording OFF, securely deletes SQLite cells and vacuums the local database. Enable recording or load fixtures to record again. This is not a guarantee of forensic erasure from flash, and it does not remove ATAK's own copies.

## ATAK representation

Trail output uses ATAK's supported `u-d-f` free-form drawing: two or more ordered `<link point="lat,lon[,hae]"/>` vertices, one UID `ouitak-trail-<aircraft presentation UID>`. Repeated sends replace that drawing; separate aircraft have separate drawing UIDs. A trail is separate from the `a-u-A` live aircraft marker. There is no permanent marker per observation. Lines merely connect observed vertices; they are not additional observations or an inferred flight path.

Default trail limit is 20 positions. UI cycles 20/50/100/5; core accepts 2–100. Retention is applied before the point limit. White and pale-yellow strokes fit both Android ARGB and the conservative signed-byte color types in the public Free Form schema. Simulated drawings are labeled `SIM HISTORY`; replay drawings `REPLAY HISTORY`. Their remarks identify the parent UID and first/last original timestamps. The broad synthetic fixture geometry makes both trails visible on the offline globe; it does not model realistic flight performance. ATAK may calculate unusual centre/elevation readouts for such large drawings; use the actual vertices and reported per-aircraft HAE, not a drawing centre, as observations.

Control-station, takeoff and receiver reports use separate `b-m-p-s-m` spot markers, with role and aircraft ID in the label and parent UID, independent report timestamp, provenance and validity in detail. Unavailable locations produce no marker. Receiver reports may share a coordinate when the same receiver observed both aircraft, but their associations and UIDs remain distinct. All claims are labeled unverified, and simulation/replay are explicitly marked. Reported-location expiry defaults to 300 seconds in the UI (60/300/3600 options), separate from live aircraft stale time (default 60 seconds) and history retention.

Historical drawings are archived ATAK objects. Their CoT stale time is **not** a guaranteed client-side removal timer. Retention controls OUI-TAK storage and vertices on subsequent publication; already imported objects may remain in ATAK until updated or removed there. Local deletion cannot erase another application's history. Inspect/remove retired objects in ATAK when needed.

## Replay controls

Open **History and replay controls**, load the eight-observation/two-aircraft synthetic fixture or use stored history, select speed, then **Replay stored history**. UI speeds: 0.5×, 1×, 2×, 4× and 16×; core permits 0.25×–64×. **Pause** freezes the monotonic replay clock, **Resume** continues it, and **Reset** rewinds to READY. Resume after Reset starts at the beginning. A completed replay remains idle with Reset/Stop available. Paused/READY/finished replay does not poll or hold a wake lock; the foreground notification remains until explicit Stop. Use **Monitoring controls → Stop Monitoring** or the notification action to end the session.

Original observations and timestamps never change. Replay uses current CoT event/stale timestamps and preserves original observation time in detail. Every replay session has a random session UUID in `ouitak-replay-<session>-<live UID>`; its aircraft, trails and associated-location markers cannot overwrite live identities. Reset keeps that session's identities and uses the researched `t-x-d-d`/`__forcedelete` task to remove its previous replay presentation before restarting. Deletion targets are restricted to OUI-TAK presentation namespaces. UDP and deletion have no acknowledgement; tested ATAK ingestion is recorded separately. Stopping/restarting or process death may leave retired replay objects in ATAK; remove them there. Pausing beyond marker stale time can make markers fade according to ATAK preferences.

Replay is driven by the same user-started foreground service as monitoring. It never records replay frames back into local history. No interpolation, RF transmission, TAK Server, MQTT, Meshtastic, account or internet connection is needed.

## Pinned ATAK sources and validation

Tested ATAK-CIV SDK developer APK: 5.5.1.8 (7f381e4d), Android 14 x86_64. Source revision `7d583c8834f7f432da8dfc4a1e7c61d7df65e846`:

- [DrawingShapeImporter](https://github.com/TAK-Product-Center/atak-civ/blob/7d583c8834f7f432da8dfc4a1e7c61d7df65e846/atak/ATAK/app/src/main/java/com/atakmap/android/drawing/importer/DrawingShapeImporter.java): type and update semantics.
- [EditablePolylineImporter](https://github.com/TAK-Product-Center/atak-civ/blob/7d583c8834f7f432da8dfc4a1e7c61d7df65e846/atak/ATAK/app/src/main/java/com/atakmap/android/drawing/importer/EditablePolylineImporter.java): at least two linked points, no vertex marker per position.
- [CotDeleteImporter](https://github.com/TAK-Product-Center/atak-civ/blob/7d583c8834f7f432da8dfc4a1e7c61d7df65e846/atak/ATAK/app/src/main/java/com/atakmap/android/cotdelete/CotDeleteImporter.java): explicit link UID plus optional forced deletion, subject to client policy.
- [Public Free Form XSD](https://github.com/deptofdefense/AndroidTacticalAssaultKit-CIV/blob/889eee292c43d3d2eafdd1f2fbf378ad5cd89ecc/takcot/xsd/Drawing%20Shapes%20-%20Free%20Form.xsd) and its relative includes are downloaded separately, never redistributed.

Run `java tools/ValidateCot.java BASE.xsd TRACK.xsd EVENT.cot ...` for aircraft, replay, spot and removal events. Run `java tools/ValidateDrawing.java 'Drawing Shapes - Free Form.xsd' TRAIL.cot ...` for full drawing validation with local includes. No network resolution is allowed by the validators. The generic base schema permits project-owned detail extensions; it does not establish that ATAK interprets those extensions. Schema checks do not prove receiver rendering; see [verification](VERIFICATION-0.4.md).
