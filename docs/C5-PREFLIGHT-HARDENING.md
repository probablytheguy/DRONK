# DRONK / SUSPECTRUM C5 preflight hardening

Base: `380419c`. Firmware application version: **0.2.1**, hello label `C5-0.2-preflight`. USB receiver protocol stays **version 1**. This change does not flash hardware, open physical USB, contact TAK, or change dashboard features.

## Defects and fixes

The previous wrapper required the ODID outer header and every member to have version 2. It also called upstream `decodeMessagePack`, whose content check rejects reserved member types. The pinned upstream member decoder (`components/odid/vendor/opendroneid.c`, `decodeOpenDroneID`) does not itself check the version nibble. Upstream documents versions 0, 1 and 2 and identifies the System timestamp as a version-2 addition: [protocol notes](https://github.com/opendroneid/opendroneid-core-c#protocol-versions). This is compatible-layout support, not a claim that every historical/private Remote ID variant is supported.

The wrapper now validates pack type, 25-byte member size, nonzero bounded count, complete declared extent, and zero trailing padding **before** passing complete local member copies to the pinned member decoder. The vendored decoder/license files remain unchanged. Outer versions 0–2 are accepted. Unknown outer versions are classified unsupported. Complete reserved-type 6–14 or future-version member slots can be skipped without interpreting their bodies. Nested packs reject regardless of version, preventing recursion into the upstream un-sized packed path. Nonzero trailing data, truncated slots and incorrect transport lengths reject. Padding is allowed only inside the declared RID payload, not as a way to bypass a broken IE/NAN attribute length.

Known-message decoder failures, duplicate Basic ID types, duplicate auth pages, duplicate singleton messages and upstream capacity violations still reject the pack. At least one supported member must decode to count a decoded pack. A detection still needs a printable supported typed Basic ID and independently valid aircraft Location from that same pack. Skipping the only ID or Location cannot produce an aircraft. Multiple supported typed IDs remain ambiguous and are held. Operator data never fills aircraft coordinates. Legacy System timestamps are unavailable for versions 0/1; version-0 operator altitude is conservatively unavailable so reserved bytes cannot become invented measurements. A new optional `odid_protocol_version` observation field identifies the outer ODID version, distinct from JSON protocol v1.

The previous loop slept one tick after every packet, imposing an approximately 100-packet/s scheduling ceiling at 100 Hz regardless of decoder speed. The consumer now uses the portable production `rid_drain` helper: first receive waits up to 100 ms, subsequent receives have zero wait, and work drains until empty, 32 packets, or 5 ms. Only hitting a slice bound triggers a one-tick idle opportunity. Empty queues naturally block in the next receive. Watchdog feed and status service occur between bounded slices. USB writes remain in their separate higher-priority output task with bounded buffers/waits. This removes the per-packet delay while retaining progress for idle/system/watchdog tasks under saturation.

## Counter semantics

Existing fields remain; all additions are inside the existing `counters` object and pass through the Linux hub's existing generic counter map. No production host parser change was necessary.

| Field | Meaning / unit |
| --- | --- |
| `rf_frames` | Management-frame callbacks delivered by the configured driver, including invalid/oversized/dropped frames. Not every ambient RF packet. |
| `candidate_frames` | Dequeued frames containing a recognized RID envelope; some malformed candidates count too. Queued-but-dropped frames cannot be classified. |
| `decoded_packs` | Valid supported content decoded without known-member failure, including incomplete-for-detection packs and packs with safely skipped slots. |
| `decoded_messages` | Successfully decoded supported members of successful packs; skipped slots do not count. |
| `malformed_frames` | Driver metadata errors or malformed frame/pack structure, lengths, nested packs or nonzero padding. |
| `unsupported_versions` | Unsupported header occurrences: unknown outer version, or each skipped future-version member. Not unique versions; can coexist with a successful pack. |
| `decode_failures` | Known-member decoder/content failure, including duplicate/capacity violations. |
| `queue_drops` | Actual failed zero-wait packet `xQueueSend` attempts. Never inferred by subtracting unrelated counters. |
| `usb_output_drops` | Failed record enqueue/unavailable writer, short/timed-out write, or record discarded during resynchronization. Attempt failures, not exact end-to-end lost-record counts or delivery acknowledgements. |
| `skipped_messages` | Complete reserved/future-version slots skipped without body interpretation. |
| `errors` | Existing aggregate metadata/structural/decode/initialization failures; benign unsupported/skipped slots are counted separately. |
| `output_errors` | Existing aggregate formatting and output failures. Formatter failures do not also count as USB drops. |
| `oversize_drops`, `detections` | Existing oversize packet and successfully formatted eligible observation counters remain. |

Counters have different populations/units and are not a mutually exclusive partition of RF frames. USB delivery is lossy and unacknowledged. A short record can partially arrive or be completed by a later delimiter; no byte-perfect delivery claim is made.

## Software validation

- **30 firmware host tests**, including Beacon/NAN versions 0/1/2, reserved/future slots, zero/nonzero padding, every new fixture prefix, maximum pack count, malformed known messages, exact counter classification, poisoned legacy System reserved bytes, independent locations and existing bounds/identity cases.
- **ASan/UBSan:** every prefix and all 256 one-byte mutations at every position of all checked-in fixtures exercise production parsing, counter accounting, full-size status/detection serialization and small-buffer rejection. The production drain helper/decoder host queue model is also compiled with ASan/UBSan. LeakSanitizer is disabled because sandbox thread inspection is restricted; ASan/UBSan halt on findings.
- **Host queue measurement:** eight-slot model, production drain helper and real synthetic Beacon decoder, with a real 10-ms host sleep at each bounded-slice yield to model a 100-Hz idle opportunity. A representative sanitized run decoded **1,280 packets in 40 slices, about 0.43 s / 3,000 packets/s**. The old per-packet tick model has a nominal 100/s ceiling. The test asserts packet/time slice bounds, first-wait-only behavior, exact overflow accounting (100 attempted enqueues, 8 retained, 92 drops) and progress, without a flaky absolute wall-clock speed threshold. This is a host scheduling model, **not ESP32 or RF throughput** and not a model of ESP-IDF's queue implementation.
- **75 Android/core and 19 Linux bridge tests**, plus **17 DRONK Python HTTP/control/process/PTY tests** and **10 existing OUI-SPY Linux adapter tests**. A new fixture verifies reset reason, all split counters, status-only/no-aircraft behavior, partial-line discard, reconnect into a new session and retired-session rejection. Existing same-session replay, multi-receiver, disconnect and legacy behavior remain covered.
- **ESP-IDF v5.5.4 builds:** channel 6 and channel 149, target esp32c5; both final application images are **702,448 bytes** (0xab7f0), with 33% of the 1-MiB app partition free. Both configurations confirm 100-Hz ticks and 10-second task-watchdog panic/reboot. Fresh isolated build directories were used, followed by final source/version rebuilds. No flash/monitor command was run. Build artifacts are ignored local outputs.

Reproduce from the repository root:

```bash
python3 -m unittest discover -s firmware/esp32-c5-receiver/tests -p test_receiver.py -v
./gradlew --offline :linux-bridge:test :linux-bridge:installDist :app:testDebugUnitTest
python3 -m unittest discover -s tools/dronk/tests -v
python3 -m unittest discover -s tools/milestone-0.8 -p test_linux_serial_bridge.py -v
```

Build without hardware:

```bash
cd firmware/esp32-c5-receiver
source ~/esp/esp-idf-v5.5.4/export.sh
idf.py -B build-preflight6 -D SDKCONFIG="$PWD/build-preflight6/sdkconfig" build
idf.py -B build-preflight149 \
  -D SDKCONFIG="$PWD/build-preflight149/sdkconfig" \
  -D 'SDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.channel149' build
```

For another clean run, use new directory names. Offline fixtures can be regenerated using the existing C generator plus `tests/make_preflight_fixtures.py`; all data is explicitly synthetic and is never sent over RF.

## Reliability findings and remaining hardware gates

**Verified in source/build/offline tests:** `app_main` initializes monitoring on power-on without host input; the receive/decode loop runs indefinitely; USB output uses a separate bounded queue/task and cannot block the decode path on a host write; the decode task is subscribed to a 10-second panic/reboot watchdog with uptime/reset reporting; batch limits provide idle opportunities; host reconnection/session/counter behavior is fixture/PTY tested. Application Wi-Fi call-site inspection retains NULL mode, fixed channel and management-only promiscuous reception. There are no application active-scan, association, AP, raw TX, NAN-participation or BLE transmit calls. The change introduces no host receiver commands or configuration writes.

**Not physically verified:** standalone on-air reception without a host; independently powered USB data detach/reattach continuity; real FreeRTOS/USB/radio saturation and heap/watchdog behavior; actual watchdog reset and USB enumeration behavior; 2.4/5-GHz reception/Avata compatibility; antenna/power integrity; absence of every possible driver-level RF emission; TAK application receipt. A periodic status only proves a running loop/configuration state, not RF reception.

New sessions can be accepted after a real transport reconnect even if hello was missed. While an old session stays bound without a transport reconnect, the existing host requires hello before switching sessions; retired sessions reject. Whether this board can reboot without a detectable USB disappearance and lose hello is a hardware gate, not silently assumed safe. Removing USB also removes power if it is the board's only supply. TTY opens may reset the board despite no explicit DTR/RTS/reset command; no zero-reset guarantee is made. Follow the [hardware checklist](../firmware/esp32-c5-receiver/HARDWARE-BRINGUP.md) only after separate hardware authorization.

### Local final binary SHA-256

These identify this build's ignored local artifacts, not a flashed device or a reproducibility guarantee:

| Build | SHA-256 |
| --- | --- |
| `build-preflight6/ouitak_c5_receiver.bin` | `9d4d90aa2d90586089c4dec596552ca657b10568fe14da9525b6b4ecb18050c7` |
| `build-preflight149/ouitak_c5_receiver.bin` | `8828fc8f67c8506cbdf65b2deed6dc524104d564edab4031787b4b033f6e8fdb` |
