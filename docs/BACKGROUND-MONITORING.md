# User-started monitoring and USB boundary

**Start Monitoring** explicitly starts simulated aircraft updates in an Android foreground service. The service owns its worker thread, live tracker, SQLite access and CoT delivery; closing the Activity or opening ATAK does not own or stop that work. The ongoing low-importance notification opens OUI-TAK and offers **Stop Monitoring**. No boot receiver, background auto-start, sticky restart, scheduler or battery-optimization exemption is installed. `START_NOT_STICKY` and null-intent shutdown prevent silent monitoring restart after process loss. Android/user force-stop ends monitoring; the user must start it again.

Simulation publishes every two seconds, maintaining one live marker and one bounded trail per aircraft. Replay uses the same service with current display timestamps and a separate identity namespace. Pause/READY/completion is idle without polling or a wake lock; explicit Resume/Reset/Stop remains available. Active processing uses a bounded, renewed partial wake lock released on Stop. SQLite, socket operations and USB rescans run on the worker, not the main thread. Notifications/preferences are throttled rather than updated at replay's 200 ms polling cadence. This is an engineering prototype, not a measured physical-device battery/reliability claim.

## Android permission and lifecycle policy

The minimum remains API 26; compile/target is API 37. On API 26+, start is initiated from the visible Activity. The manifest declares the base foreground-service permission and `specialUse|location` service types. API 34+ simulation/replay uses `specialUse` with an explanatory subtype property, because it has no actual USB serial connection or data-sync server. It does not misuse `connectedDevice` or `dataSync` types. Any future publication must review applicable special-use distribution policy; no publication has occurred.

Notifications request `POST_NOTIFICATIONS` on API 33+. Android can allow a foreground service after notification permission denial; the app does not promise notification drawer visibility in that case. Optional **Start Monitoring with receiver GPS** requests fine/coarse location while visible; GPS mode requires fine permission and adds the location service type. It requests GPS updates at 10-second/10-metre thresholds and stores available fixes as receiver reports, never aircraft/operator points. No background-location permission is requested. Missing permission/provider yields a visible diagnostic, not invented coordinates.

For target API 37, the app declares and requests `ACCESS_LOCAL_NETWORK` before delivery. This is a conservative local-UDP permission gate; no loopback exemption is assumed. Android foreground-service timeout callbacks stop the service. Start denials are shown, and GPS/background starts are not attempted automatically. Android 14 API 34 is the runtime-tested version. Android 15–17 permissions, power behavior and vendor policies still need physical/platform validation; compiling against 37 does not prove runtime reliability there.

Primary references: [foreground service types](https://developer.android.com/develop/background-work/services/fgs/service-types), [background-start restrictions](https://developer.android.com/develop/background-work/services/fgs/restrictions-bg-start), [local network permissions](https://developer.android.com/privacy-and-security/local-network-permission), and [notification permission](https://developer.android.com/develop/ui/views/notifications/notification-permission).

## USB discovery only; real parsing disabled

The service lists USB devices, checks Android grants, requests access only from an explicit user control, and rescans after supported attach/detach/permission notifications and during active monitoring. Disconnects do not end simulation; reconnects update status. Broadcasts trigger an authoritative `UsbManager` rescan; their extras are never trusted as sensor data. No port is opened, serial driver configured, device identifier logged, or incoming bytes parsed. No device is assumed to be OUI-SPY merely because it is attached. Physical reconnect/grant behavior remains unverified.

The upstream Unified Blue repository was inspected at revision **`6e5e2829540925ff1ab5fa6d0d365378818ed56e`**, dated 2026-10-11. This is a research pin, not a verified version running on target hardware:

- [Sky Spy wrapper](https://github.com/colonelpanichacks/oui-spy-unified-blue/blob/6e5e2829540925ff1ab5fa6d0d365378818ed56e/src/mode_skyspy.cpp)
- [Underlying Sky Spy implementation](https://github.com/colonelpanichacks/oui-spy-unified-blue/blob/6e5e2829540925ff1ab5fa6d0d365378818ed56e/src/raw/skyspy.cpp)

The inspected code initializes Serial at 115200 and has a `send_json_fast` path formatting fields named `mac`, `rssi`, `drone_lat`, `drone_long`, `drone_altitude`, `pilot_lat`, `pilot_long` and `basic_id` into a bounded buffer before printing. Scanning/status JSON and diagnostic output share Serial. This observation is not a complete, versioned USB contract. Buffer truncation/escaping, identifier type, field availability, origin timestamps, host USB configuration and exact framing need captures. The apparent `altitude_msl` variable is populated from OpenDroneID `AltitudeGeo`; its name alone cannot establish the altitude reference. Pilot/control-station semantics and unavailable sentinels also need verification. No takeoff/receiver schema is invented from these names.

The actual device firmware revision is unknown. A real parser stays disabled until a pinned hardware revision and representative raw captures establish framing, identifier/aircraft association, timestamp policy, units/reference, missing-data behavior and malformed-input handling. USB/Meshtastic interfaces remain independent future adapters.

## Reproducible emulator and physical checks

1. Start ATAK with loopback UDP 4242 enabled. Turn on airplane mode and disable Wi-Fi. Start Monitoring explicitly in OUI-TAK.
2. Open ATAK. Inspect at least two consecutive aircraft coordinates and each aircraft's single trail; packet submission alone is insufficient. Confirm the ongoing service in Android diagnostics.
3. Turn the screen off, check that the service remains active and the submission count advances, then reopen OUI-TAK. Check actual receiver changes after waking. An emulator sleep test is not physical Doze evidence.
4. Stop through the UI or notification. Confirm the count stops and the service/wake lock is gone. Force-stop/reopen must not restart monitoring.
5. On a physical Android 14–17 device, repeat extended screen-off/Doze, permission-denial, OS kill, battery/vendor policy and optional GPS tests. When hardware is available, attach/detach/reconnect and grant/revoke USB permission; the UI must continue to say parsing disabled until the contract is verified.

See [verification evidence](VERIFICATION-0.4.md). No physical background reliability, real USB parsing or mesh delivery is claimed.
