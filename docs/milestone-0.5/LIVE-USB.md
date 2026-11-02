# Live Sky Spy USB ingestion

The live monitor targets the ESP32-S3 native USB CDC device `303a:1001`, reads at 115200 8N1, and accepts newline-delimited Sky Spy JSON through the existing bounded `LineFramer` and `OfflineSerialParser`.

In OUI-TAK, open **Monitoring controls**, choose **Start Live OUI-SPY USB**, then grant access to the attached ESP32-S3. The status display reports accepted, ignored, and rejected serial records. Simulation and replay are separate monitor modes.

The configurable legacy association policy is named **Legacy IDs: provisional markers enabled/disabled** and is enabled by default. When enabled, a record creates a provisional marker only when the `basic_id` is nonempty and aircraft latitude/longitude are valid. The ID type remains `PROVISIONAL_UNTYPED`; `mac` is never used as aircraft identity. Pilot coordinates remain unresolved. Provisional UIDs include a persisted per-installation receiver namespace to avoid merging tracks from independent receivers.

The firmware schema carries no broadcast timestamp. Provisional CoT event time therefore uses local receiver time and includes `timeBasis="receiver"` and `receivedAt`; it does not claim a verified broadcast time. Marker callsigns and remarks say `PROVISIONAL`.

The USB host configures CDC line coding to 115200 8N1 and deliberately does not assert DTR/RTS. It reconnects after disconnects and discards incomplete buffered frames when a session ends. Physical USB and ATAK delivery have not yet been verified; perform that check on a connected Android device before treating the bridge as field validated.
