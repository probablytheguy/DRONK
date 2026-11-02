# ESP32-C5 receiver: capability and architecture investigation

Date: 2026-10-28. Phase: investigation complete; firmware implementation has not started.

Scope: a separate ESP-IDF project for the Seeed Studio XIAO ESP32-C5, passive ASTM F3411 reception, no changes to upstream OUI-SPY firmware. No USB enumeration, device open, flash, RF transmission, or hardware changes were performed. Prior untracked milestone 0.5 capture work is outside this change.

## Evidence and capability boundaries

Use ESP-IDF **v5.5.4** as the initial reproducible API baseline, not an unpinned `stable`/`latest` URL. A successful compile, successful channel selection, received RF packets, and decoded aircraft messages are four distinct validation levels. None of the latter three has been established on the ordered board.

| Capability | Documentation/source evidence | Remaining validation |
| --- | --- | --- |
| Wi-Fi capture | C5 guide documents promiscuous management frames and `WIFI_MODE_NULL`. Driver callbacks run in the Wi-Fi task: copy to a bounded queue, decode elsewhere. | Verify actual callback delivery on both bands; confirm packet length/FCS and metadata handling. |
| Dual-band | C5 has 2.4/5 GHz modes; AUTO does not receive both simultaneously. | Establish reliable single-channel reception before hopping. |
| Channel hopping | `esp_wifi_set_channel()` is documented after start/before stop; avoid connection/AP scan activity. Channel selection can change bands in AUTO mode. | Check every return value, read back actual channel/band, measure dwell/retune losses. |
| Country/channel coverage | Country configuration includes a 5-GHz channel mask. | Validate configured regional allowlist against driver-supported channels; do not enable all DFS channels by assumption. |
| BLE | C5 GAP reference documents passive and extended scanning APIs. | Select one host stack, enable correct controller options, verify legacy/extended/coded PHY separately. |

Sources:

- [C5 Wi-Fi driver, v5.5.4: sniffer, bands and country behavior](https://docs.espressif.com/projects/esp-idf/en/v5.5.4/esp32c5/api-guides/wifi.html#wi-fi-sniffer-mode)
- [C5 Wi-Fi API, v5.5.4: channel, band and country configuration](https://docs.espressif.com/projects/esp-idf/en/v5.5.4/esp32c5/api-reference/network/esp_wifi.html)
- [C5 GAP API](https://docs.espressif.com/projects/esp-idf/en/stable/esp32c5/api-reference/bluetooth/esp_gap_ble.html) (the consulted stable page identifies v6.1; verify signatures against the selected v5.5.4 headers before implementation)
- [Espressif scanning API guidance](https://docs.espressif.com/projects/esp-techpedia/en/latest/esp-friends/advanced-development/ble-application-note/broadcasting.html)

Passive design: no AP, association, active AP scan, NAN participation, raw transmit, BLE advertisements, scan requests, or connections. Prefer Wi-Fi NULL/promiscuous mode. Receiving NAN action frames does not require joining a NAN network. BLE scan parameters must explicitly be passive; do not copy upstream Sky Spy's active-scan setting. Wi-Fi-only bring-up should precede optional BLE to isolate coexistence effects. Firmware intent is receive-only; absence of unintended RF emissions still needs an external observation during hardware validation.

## Board and USB

Seeed specifies a single-core 240-MHz RISC-V processor, 384-KB on-chip SRAM, 8-MB flash and 8-MB PSRAM, external antenna, USB-C, BOOT GPIO28 and user LED GPIO27. These are vendor specifications, not measured properties of the ordered unit. Confirm the delivered revision, flash/PSRAM variant, antenna coverage and connector routing. Do not reuse S3 pin definitions. Initial queues and decoder state should fit internal RAM; PSRAM is optional until supported settings and successful initialization are verified. Bound packet queues, record length, aircraft/session state and JSON allocation; report free/minimum heap and drops.

[Seeed XIAO ESP32-C5 guide](https://wiki.seeedstudio.com/xiao_esp32c5_getting_started/), [Seeed serial interfaces](https://wiki.seeedstudio.com/xiao_esp32c5_pin_multiplexing/).

C5 provides hardware USB Serial/JTAG. ESP-IDF documents console selection using `CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG`, USB flashing with `idf.py flash -p PORT`, and Linux CDC names `/dev/ttyACM*`. USB D+/D- are GPIO14/GPIO13. Console buffering and sleep can disrupt output; use newline framing, a dedicated output task and no sleep during initial bring-up. USB flashing/reset/monitor commands are documentation only, not authorized hardware steps in this phase. Host serial open must retain the existing reset-risk warning; no zero-reset guarantee is claimed.

[Espressif USB console and flashing documentation, v5.5.4](https://docs.espressif.com/projects/esp-idf/en/v5.5.4/esp32c5/api-guides/usb-serial-jtag-console.html).

## Decoder choice

Select [opendroneid/opendroneid-core-c](https://github.com/opendroneid/opendroneid-core-c), a portable C decoder with **Apache-2.0** licensing, not BSD. Pin a reviewed commit and preserve its license, file copyright notices and any applicable NOTICE material when importing. This phase reviewed upstream interfaces; no dependency has been imported yet.

Available APIs include `decodeOpenDroneID`, `decodeMessagePack`, `odid_message_process_pack`, and `odid_wifi_receive_message_pack_nan_action_frame`. The reviewed header has a NAN frame receiver but no matching complete Beacon receive function. Beacon handling therefore needs a small bounds-checked 802.11/vendor-IE adapter feeding the available message-pack decoder. BLE needs bounds-checked AD-element/service-data extraction, not hard-coded advertisement offsets. Reject malformed/truncated input before passing it to APIs without a length parameter. Clear decoder state per packet; do not let validity flags or fields leak across packets.

[License](https://github.com/opendroneid/opendroneid-core-c/blob/master/LICENSE), [decoder APIs and validity structures](https://github.com/opendroneid/opendroneid-core-c/blob/master/libopendroneid/opendroneid.h), [Wi-Fi framing structures](https://github.com/opendroneid/opendroneid-core-c/blob/master/libopendroneid/odid_wifi.h).

The library also contains encoders/builders; their presence is not permission to transmit. Offline fixtures may exercise encoded bytes without RF. Synthetic fixtures must be labeled; actual Avata PCAP fixtures are not yet available.

## Existing architecture and integration boundaries

Repository modules: Android `:app`, JVM `:shared-core`, JVM CLI `:linux-bridge`.

Current production path:

```
Linux POSIX TTY reader -> binary chunk/connection envelope -> LinuxBridgeMain
Android Esp32UsbSerialTransport -> MonitoringService
    -> LiveSerialProcessor -> SerialIngestionPipeline
    -> OfflineSerialParser / LineFramer / StrictJson / CandidateValidator
    -> legacy provisional association -> AircraftTracker -> CotGenerator
    -> separately configured CotTransport
```

Relevant source locations:

- `tools/milestone-0.8/linux_serial_bridge.py`: raw 115200 8N1 adapter; one selected device; capture-only default; explicit live/TLS forwarding; read-error stop when node remains, reconnect after disappearance. Open/configuration failures can still retry. Do not quietly change legacy retry behavior while adding a protocol.
- `linux-bridge/src/main/kotlin/org/ouitak/bridge/LinuxBridgeMain.kt`: one processor with host-derived namespace, one stdin stream; logs assume legacy fields and unavailable broadcast time.
- `shared-core/src/main/kotlin/org/ouitak/app/core/serial/OfflineSerialParser.kt`: bounded 4096-byte LF/CRLF frames, strict UTF-8/JSON, eight-field legacy schema, exact malformed-status exception. New nested records would currently be ignored as status if no legacy keys match.
- `SerialCandidate.kt`, `LegacyAssociationPolicy.kt`: legacy candidates lack typed identity and evidence; provisional tracks require Basic ID and aircraft coordinates. Legacy pilot coordinates remain unresolved.
- `CandidateAssociation.kt`: evidence is deliberately out-of-band and bound to one candidate. Do not relabel incoming serial claims as that trusted evidence.
- `RemoteIdObservation.kt`, `Detection.kt`, `ReportedLocation.kt`: typed identity, independently modeled aircraft/control-station/takeoff/receiver roles, HAE altitude, observation/receipt chronology. Source currently only distinguishes simulation/OUI-SPY USB; receiver and RF provenance need an additive representation.
- `AircraftIdentity.kt`: typed IDs can share an aircraft UID across receivers; legacy provisional IDs remain receiver-scoped. Receiver ID must not become aircraft ID. Binary session/UTM IDs are not supported by the existing textual identity boundary.
- `Interfaces.kt`: `SensorParser` and `OuiSpyUsbSerialInput` are generic declarations; the live implementation actually feeds `LiveSerialProcessor`. Integrate at that active boundary.
- `app/.../Esp32UsbSerialTransport.kt`: S3-specific VID/PID/interface discovery. Leave Android USB device support unchanged in this Linux-focused increment.
- `tools/milestone-0.8/ouitak-live`: S3-specific discovery must remain compatible; C5 discovery must be explicit and based on observed identity, not guessed VID/PID.

Integration proposal: version-dispatch before legacy classification, retain the legacy parser/policy unchanged, and route validated C5 observations through the existing normalization/tracker/CoT path. Maintain framing, decoder state, boot/sequence state and receiver attribution independently per serial stream. For initial multiple physical receivers, separate bridge processes can carry distinct provenance; a later single-process supervisor needs independent stream framing and an explicit merge/deduplication policy. No shared partial-byte buffer between devices. Forwarding remains opt-in.

## Protocol requirements to settle before firmware

Draft direction, **not an implemented or finalized wire contract**: bounded UTF-8 newline-delimited JSON with a protocol discriminator and integer version. Distinct `hello`, `status`, `observation`, and `error` records. Unknown versions reject, not legacy fallback.

- Common envelope: stable receiver ID, firmware/build and decoder revision in hello, boot/session ID, monotonically increasing sequence, receiver monotonic microseconds. Wire identity is attribution, not authentication; bind expected receiver identity to a host stream when configured.
- RF metadata: `wifi_beacon`, `wifi_nan`, `ble_legacy`, or `ble_extended`; actual receive channel/frequency in MHz, RSSI dBm, transmitter address as transport metadata only. BLE channel/frequency stays unavailable unless the selected API reports it; never substitute the Wi-Fi channel or guess the BLE advertising channel.
- Aircraft: exact Basic ID and ODID ID/UA type; decoded position with availability/validity and HAE altitude units/reference. Preserve accuracy/status fields where supplied. Unknown/sentinel coordinates are absent/null with an explicit reason, never filled with zero or operator coordinates.
- System/operator location: include only decoded System data with operator-location type and its own timing/validity. Do not call takeoff location a current control-station position. Operator ID is not aircraft Basic ID.
- Time: distinguish receiver uptime, host receipt UTC and raw broadcast timestamps. No onboard GNSS/NTP clock is assumed. ODID Location time is not a complete UTC date; keep its raw value and interpretation separate. If receipt time is used for presentation, label that policy. Never fabricate broadcast UTC or convert uptime directly to epoch time.
- Association: initially promote only supported typed Basic ID plus valid Location from the same decoded pack. Individual BLE messages can be emitted as partial observations without generating a track; any later bounded cross-message association requires documented freshness/address/session rules and tests.
- Status: successful/failed tune operations, actual channel/band, RX management/Beacon/NAN/BLE counters, decoder successes/rejections, queue/serial drops, uptime and heap. A timer heartbeat alone is not proof of RF reception. Retain RF metadata through host ingestion even if CoT cannot represent all of it yet.

## Baseline tests and tooling

Inspected offline coverage: strict JSON, UTF-8, line splits/overflow, disconnect epochs, ID/coordinate validation, no pilot reassociation, provisional namespaces, synthetic/live separation, tracking and CoT. Linux processor tests are under `linux-bridge/src/test`; parser tests are currently under Android `app/src/test`, even though implementation is shared.

Executed without USB hardware:

```
python3 -m unittest discover -s tools/milestone-0.8 -p 'test_linux_serial_bridge.py' -v
./gradlew --offline :linux-bridge:test :app:testDebugUnitTest
```

Python: 10 passed (mocks and a pseudo-terminal only). Gradle: BUILD SUCCESSFUL; test tasks were UP-TO-DATE, so this confirmed cached baseline results, not fresh execution. Initial sandbox Gradle initialization failed to determine a wildcard IP; the same offline command succeeded outside that sandbox. An Android SDK XML-version warning remains. No Android instrumentation or live forwarding was run.

`idf.py`, `riscv32-esp-elf-gcc`, `cmake`, and `ninja` were absent from PATH; usual `~/esp`, `~/.espressif`, `/opt/esp` directories were absent. Python and Java exist. No firmware toolchain was installed and no firmware build attempted in the investigation phase.

Planned firmware build commands, after source exists and host prerequisites are installed:

```bash
mkdir -p ~/esp
git clone --branch v5.5.4 --recursive https://github.com/espressif/esp-idf.git ~/esp/esp-idf-v5.5.4
cd ~/esp/esp-idf-v5.5.4
./install.sh esp32c5
. ./export.sh
cd ~/Projects/OUI-TAK/firmware/esp32-c5-receiver
idf.py set-target esp32c5
idf.py build
idf.py size
```

These are future installation/build instructions; the project directory does not yet exist. OS prerequisites and supported Python versions must be checked before installation. [Official pinned setup guide](https://docs.espressif.com/projects/esp-idf/en/v5.5.4/esp32c5/get-started/linux-macos-setup.html). No `flash`, `monitor`, or hardware commands belong in automated checks.

## Implementation sequence and hardware gates

Commit this investigation first. Then separate reviewable commits for (1) protocol specification and fixtures; (2) isolated ESP-IDF project, USB diagnostics/channel scheduler/status; (3) pinned decoder and bounded Beacon/NAN adapters plus host-native tests; (4) versioned shared-core/Linux ingestion and compatibility tests; (5) build evidence and bring-up checklist. Do not include previous milestone capture files in those commits.

Offline tests must cover Beacon/NAN valid and truncated frames, malformed IE/AD lengths, unrelated traffic, decoder failures, missing/unsupported ID, absent/invalid coordinates, location role/type, timestamp wrap/unknown UTC, partial records, oversized records, unknown versions, reboot/sequence changes, interleaved independent receiver streams and unchanged legacy fixtures. Add a scan-scheduler test and queue-overflow accounting. BLE support should only be advertised for implemented, enabled, tested paths.

Hardware bring-up gates (future operator session):

1. Confirm delivered board revision, antenna and memory variant without assuming S3 wiring. Identify C5 independently of existing OUI-SPY.
2. Review the built configuration and binary before separately authorized flashing. Never erase existing OUI-SPY or reuse its device path by default.
3. Verify USB JSON framing, boot ID, receiver ID, diagnostics, resets and backpressure. Confirm no firmware Wi-Fi/NVS restore behavior was introduced.
4. Observe existing ordinary management traffic on a fixed allowed 2.4-GHz channel; verify RX counters increase independently of decode counters.
5. Repeat on a fixed allowed 5-GHz channel, initially channel 149 where region/driver permit. Record successful tune/readback and actual RX evidence; an API success alone does not pass this gate.
6. Compare against an independent receiver/PCAP for the original Avata: establish actual channel, transport and ODID payload before interpreting missing detections. First-generation Avata 5.8-GHz reception remains a target hypothesis, not evidence from this new board.
7. Verify Beacon/NAN decoding and reported values against captured bytes; label provenance and limitations. No spoofed/test RF frames.
8. Measure hopping loss, queue drops, heap/stack, serial throughput and optional Wi-Fi/BLE coexistence. Confirm absence of active scan/association/advertising RF behavior independently.
9. Demonstrate multiple receiver attribution and disconnect isolation locally before any separately selected TAK forwarding. No real-world reception claim until corresponding hardware evidence exists.
