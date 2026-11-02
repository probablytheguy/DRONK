# DRONK / SUSPECTRUM operating model

SUSPECTRUM is the independently powered ESP32-C5 passive receiver. DRONK is the manually operated laptop monitoring/TAK service. The Command Center is its local supervisor and log console. These have separate lifetimes.

```text
Power -> SUSPECTRUM boot -> fixed-channel passive management-frame reception
                          -> bounded packet queue -> bounds-checked ODID decoder
                          -> bounded output queue -> USB Serial/JTAG output task
                                                    (host optional, output lossy)

Manual console launch -> localhost HTTP/SSE supervisor (host STOPPED)
  Start DRONK -> owned host_worker.py process -> read-only USB readers
                   -> JVM DronkReceiverHub -> shared tracker / CoT -> optional TLS TAK
                   -> structured telemetry -> SUSPECTRUM logs / receiver cards
  Stop DRONK -> close USB descriptors, end JVM/TLS, await worker exit
                console stays available; receiver RF state becomes NOT OBSERVED
```

## Receiver invariants

The receiver starts passive monitoring automatically and continues indefinitely while powered. There is no host handshake, inbound command parser, active Wi-Fi scan, association, beacon transmission, RF control endpoint, automatic channel hopping or BLE activation. A USB-only power source cannot keep a board running after physical USB removal: continuity requires a suitable independent supply, subject to the board's hardware validation.

The packet callback only copies bounded frames into a bounded zero-wait queue. The consumer drains at most 32 packets or 5 ms per slice, then yields at the slice bound; it no longer sleeps after every packet. Decoding never performs USB writes. A separate four-record output queue feeds a dedicated USB task using a 4-KiB driver buffer and bounded 20-ms write waits. Backpressure drops output rather than blocking decoding; `output_errors` counts formatting, enqueue and short-write failures. It does not acknowledge host delivery. Partial writes trigger a newline resynchronization attempt before the next record. Counters and `uptime_us` continue without a reader; retained output is finite and not a complete offline detection history.

The decode task subscribes to ESP-IDF's task watchdog with a 10-second timeout and panic/reboot enabled. Each new boot has a new session ID and repeats numeric `reset_reason` and uptime in every protocol record, including periodic status. An initialization/configuration failure still fails closed into `stopped_error` without destructive NVS erasure or a radio reconfiguration loop. A responsive but ineffective radio is not diagnosed by the task watchdog. Periodic scanning status proves a running loop/configuration state, not reception of RF or Remote ID.

Protocol v1 keeps its existing discriminator and parsing rules; reset reason is an optional additive host field for older firmware compatibility. Existing OUI-SPY and Android paths remain available. Identity, validated aircraft/operator coordinates, duplicate suppression, stale tracking and CoT generation stay in the existing shared core. No positions are inferred and RF counts never become aircraft detections.

## Host and console invariants

No boot/login service or desktop autostart is installed. Launch the console manually, then Start DRONK. The console starts STOPPED even with configured receivers or `--demo`; it opens no devices until Start. Stop controls the actual monitoring process, closes serial connections and ends forwarding. It does not command the ESP32 to stop, reset, change channel or power off.

The console retains bounded logs/history and last-known receiver telemetry while stopped. Host state (STOPPED, STARTING, RUNNING, STOPPING, ERROR) and receiver USB state (CONNECTED, DISCONNECTED, NOT OBSERVED) are independent. Old counters, band/channel and uptime are explicitly last reported values, not proof the device remains powered or scanning. Startup PID and unexpected process exits are visible. Crashed workers are not automatically restarted. USB reconnect behavior remains guarded: wait for actual disappearance/reappearance; do not repeatedly reopen a node with a persistent error. A manual Start can open a selected TTY and may reset the receiver despite no explicit DTR/RTS/reset operations; no zero-reset guarantee exists.

The map and aircraft register are removed. The main display is concise SUSPECTRUM event logs with expandable structured details. Receiver cards retain RF/decode/error/drop counters, USB connection, selected band/channel, uptime and reset reason. TAK transport statistics and validated detection exports remain. DEMO is explicitly synthetic and cannot open serial devices or forward. LIVE is host observation availability, not certified RF reception or aircraft identity. Browser/SSE loss shows DISCONNECTED and disables controls without silently changing the worker's lifetime.

HTTP binds only to 127.0.0.1. State changes require POST, exact local Host/Origin, a per-process CSRF token in a custom header, application/json and a bounded action-only body. Device paths, credentials, mode and forwarding policy are fixed at console launch; no RF commands exist. These defenses address browser cross-origin requests, not hostile local OS users. Use authenticated SSH local port forwarding for trusted iPhone/LAN access. See [console setup](../tools/dronk/README.md).

## Validation and remaining gates

Software verification uses synthetic offline fixtures, pseudo-terminals and localhost only. The receiver has not been flashed, no physical serial device was opened, no RF reception is claimed, and no TAK Server was contacted.

- ESP-IDF v5.5.4: clean `esp32c5` builds for channels 6 and 149; both final images are 0xab600 bytes (701,952 bytes), with 33% of the 1-MiB app partition free. Exact build commands below.
- 22 portable receiver tests, including Beacon/NAN fixtures, bounds checks, sanitizer mutations, independent locations and reset-reason serialization.
- 75 Android/core tests and 18 Linux bridge tests, including optional telemetry/backward compatibility.
- 17 Python dashboard/control/process/PTY tests and 10 existing OUI-SPY Linux adapter tests.
- Chromium desktop and iPhone-sized browser checks: actual demo Start/Stop, logs, separate receiver state, no map/register, exports, safe untrusted text, no external requests, disconnection and responsive layout. This is not physical Safari testing.

After separate flashing/hardware authorization, validate standalone startup without a host, continued RF counter/decode activity across host Stop and USB data detach/reattach with independent power, bounded output under no-reader/saturation conditions, actual watchdog recovery/new session/reset reason, TTY-open reset behavior, antenna/power/thermal stability, actual 2.4/5-GHz reception and TAK application receipt. See [hardware checklist](../firmware/esp32-c5-receiver/HARDWARE-BRINGUP.md). No real-world or zero-reset guarantees are inferred from builds/tests.

### Reproduce builds without hardware

```bash
cd firmware/esp32-c5-receiver
source ~/esp/esp-idf-v5.5.4/export.sh
idf.py -B build-autonomy -D SDKCONFIG="$PWD/build-autonomy/sdkconfig" build
idf.py -B build-suspectrum149 \
  -D SDKCONFIG="$PWD/build-suspectrum149/sdkconfig" \
  -D 'SDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.channel149' build
```

Use fresh build directories for clean builds. Both commands only compile firmware; do not append flash/monitor commands. The project pins IDF v5.5.4 and the existing OpenDroneID decoder revision/license. `sdkconfig.defaults` explicitly enables task watchdog initialization, 10-second timeout and panic; IDF's panic configuration reboots. Hardware USB driver APIs come from the pinned IDF `esp_driver_usb_serial_jtag` component, not host modem-line controls.

For the subsequent decoding/throughput hardening and its current test/build results, see [C5 preflight hardening](C5-PREFLIGHT-HARDENING.md). The results above record the operating-model milestone at `380419c`.
