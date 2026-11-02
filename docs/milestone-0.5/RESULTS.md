# Milestone 0.5 hardware and protocol discovery — 2026-10-23

Stopped at discovery. No Android parser, repository edits, commits, flashing, serial commands, web configuration requests, or GitHub publication performed.

## Repository gate

`<LOCAL_HOME>/Projects/OUI-TAK` HEAD is `779c2db99708966f1b28ffed158effd06f8c5cbd`.
The tree was NOT clean at the initial check and remains in the same state:
`?? gradle/gradle-daemon-jvm.properties` (generated JVM toolchain file, version 25).
Tracked diff is empty. The untracked file was preserved. Discovery continued as read-only investigation; the clean-tree prerequisite is not satisfied.

## Hardware

Host lsusb: Bus 003 Device 010, VID:PID `303a:1001`, Espressif USB JTAG/serial debug unit.
USB serial number: `REDACTED-USB-SERIAL`; physical USB path `3-7`.
Kernel driver: cdc_acm; serial port `/dev/ttyACM0`.
Stable path: `/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_REDACTED-USB-SERIAL-if00`.
USB descriptors: interface 0 CDC ACM control, interface 1 CDC data, interface 2 vendor-specific JTAG.
CDC data endpoints: bulk IN `0x81`, OUT `0x01`, 64-byte maximum packets; control notification IN `0x82`.
ESP32-S3 is confirmed by the captured ROM banner. USB identification alone does not establish the exact PCB/carrier revision.
Earlier kernel events include several disconnect/reconnects and one descriptor read error -71; their cause is unknown. No kernel re-enumeration events appeared in the post-capture time window.

## Capture and observed firmware

Raw sample: `serial.raw`; metadata/timing/hash: `capture.json`; capture procedure: `capture.py`.
One successful serial open, 65.094 seconds, 1282 bytes, starting 2026-10-23 19:51:45 UTC.
115200, 8N1, no hardware/software flow control, exclusive open, DTR and RTS set false before opening.
No serial payload was written and no input buffer was flushed. The initial non-root attempt was denied before opening; administrator access was needed for the root:uucp device node.

IMPORTANT: Despite following standalone upstream's no-reset open pattern, the sample begins with `rst:0x15 (USB_UART_CHIP_RESET)`. A USB-triggered reset occurred at capture start; capture-induced reset is strongly suggested, but precise driver/control-line causality is unproven. The firmware then logged `WiFi factory-reset complete - all stale config erased`. Unified source performs WiFi config erasure on boot. Therefore this operation cannot be certified as configuration-preserving. No explicit reset/configuration command was sent. No further serial opens were attempted.

Observed identifying lines:

```
ESP-ROM:esp32s3-20210327
OUI SPY UNIFIED FIRMWARE v2.0
[OUI-SPY] Stored mode from NVS: 5
[OUI-SPY] >>> STARTING SKY SPY (mode 5) <<<
[SKY-SPY] *close encounters theme*
{"   [+] Device is active and scanning..."}
```

This is direct evidence of Unified firmware running Sky Spy mode, rather than a supposition based on earlier research. Exact installed commit/build remains unknown.
The last line arrived about 60.086 seconds after open and is NOT valid JSON: it is an object-shaped string without a key/value separator.
No drone detection records appeared. The sample contains boot diagnostics, plain text, blank lines, and the malformed status line. It has 37 LF bytes, of which 28 follow CR; both LF and CRLF occur. It includes core-dump-partition and LEDC initialization errors; their operational effects are unverified.

## Source inspection

Standalone source: https://github.com/colonelpanichacks/Sky-Spy/tree/1930c9b731f9d01c161753e931377921bb9ba267
Local checkout: `/tmp/ouitak05-skyspy-upstream`.
`src/main.cpp:354` initializes Serial at 115200; `platformio.ini` agrees.
`mesh-mapper.py:13189` documents and implements pre-clearing DTR/RTS to avoid resets. Only that opening pattern was followed; its reset/recovery logic was not executed.
Standalone C5 variant adds `band` and `channel`; it cannot be assumed to describe this ESP32-S3.

Unified source: https://github.com/colonelpanichacks/oui-spy-unified-blue/tree/6e5e2829540925ff1ab5fa6d0d365378818ed56e
Local checkout: `/tmp/ouitak05-unified-upstream`.
`src/main.cpp:485` and `src/raw/skyspy.cpp:356` initialize Serial at 115200. Build config enables USB CDC on boot.
`src/main.cpp:489,592` match the Unified v2.0 and Sky Spy mode 5 banners.
`src/main.cpp:504-520` explains the observed WiFi config reset at boot.
`src/mode_skyspy.cpp` wraps `src/raw/skyspy.cpp`.

Both inspected S3 emitters produce one compact JSON object through Serial.println, with no binary length prefix, checksum, or protocol version envelope:

```
{"mac":"%s","rssi":%d,"drone_lat":%.6f,"drone_long":%.6f,"drone_altitude":%d,"pilot_lat":%.6f,"pilot_long":%.6f,"basic_id":"%s"}
```

This is a source-derived format template, NOT captured detection data. MAC is lowercase colon-separated hex; coordinate values have six decimal places, RSSI/altitude are integers. Serial.println provides line separation; actual captured text has mixed LF/CRLF. 8N1 was the capture setting; the firmware call specifies baud only and relies on Arduino defaults. Native USB delivery does not independently prove physical UART timing.

Fields derive from OpenDroneID data: BasicID[0].UASID, Location latitude/longitude, integer-cast Location.AltitudeGeo, and System.OperatorLatitude/Longitude. AltitudeGeo is meters WGS84 height above ellipsoid (see opendroneid.h), despite the internal variable name altitude_msl and README MSL wording. ID type, UA type, operator location type, validity flags, original timestamp, and transport provenance are absent from this emitter. Zero-initialized or retained values can appear; zero does not certify a valid location. Basic ID string interpolation does not perform JSON escaping. Serial text from multiple tasks may coexist with detection records.

## Confirmed versus unresolved

Confirmed on device: ESP32-S3, native USB CDC ACM interface, readable text at host setting 115200 8N1, Unified v2.0 banner selecting Sky Spy mode 5, mixed line endings, malformed legacy scanning status, no detection records in this bounded sample.

Confirmed in inspected source only: detection field layout above; serial initialization baud; omitted type/validity metadata; altitude source; boot WiFi erasure behavior. Current Unified and standalone source both emit valid `{"status":"scanning"}`, which DOES NOT match this device's malformed status line. The installed binary is therefore not established as identical to either inspected source revision.

Unknown: exact installed commit/build; actual detection schema and field behavior on this binary; WiFi versus BLE detection functionality (the banner says BLE-only while current wrapper source also enables WiFi capture); identifier semantics and location validity; timing/rate, maximum line size, interleaving under detection load; effects of the reported boot errors; exact reset cause and whether any previously stored WiFi configuration existed before boot erased its slot.

No Android USB parser should be inferred or implemented from this status-only capture. Discovery stops here.
