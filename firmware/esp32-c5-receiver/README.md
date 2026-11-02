# DRONK SUSPECTRUM ESP32-C5 receiver

Experimental firmware for **Seeed Studio XIAO ESP32-C5**, pinned to **ESP-IDF v5.5.4**. Fixed-channel passive Wi-Fi management-frame reception with ASTM/ASD-STAN Remote ID Beacon and supported NAN service-descriptor decoding. **Hardware status (2026-11-01):** ambient management-frame reception is verified on a physical board on channel 6 (2.4 GHz) and channel 149 (5 GHz), with 0 malformed frames on both. **2026-11-02:** Remote ID reception verified with the hopping build and an original DJI Avata (Wi-Fi Beacon, ODID v2, channel 149 as reported by the receiver), first without a GPS fix and then with valid broadcast positions; see the [technical journal](../../docs/DRONK-JOURNAL.md). No independent reference receiver has confirmed the channel yet. Build success is not reception evidence.

This is a separate project. Existing OUI-SPY firmware, Android application, Linux bridge, and TAK delivery are unchanged. The [version-1 JSON protocol](PROTOCOL.md) is consumed by the DRONK host path (`DronkReceiverHub`, `linux-bridge --dronk`, [DRONK Command Center](../../tools/dronk/README.md)), not by the legacy OUI-SPY bridge. The integration is tested offline only. Capture-only inspection of serial JSON is the first hardware gate; do not enable forwarding before the earlier gates pass. The first-session procedure is [C5 hardware bring-up](../../docs/C5-HARDWARE-BRINGUP.md).

## Implemented boundaries

- ESP-IDF Wi-Fi NULL mode with promiscuous management filter. No AP, association, active Wi-Fi scanning, NAN participation, RF transmit calls, BLE, Wi-Fi restore, erase, OTA, or serial command interpreter.
- Default: passive dual-band hopping (band mode AUTO) over the allowlist 1–11, 36/40/44/48 and 149/153/157/161/165, with channels 6 and 149 anchoring four of every six visits, a configurable dwell (default 250 ms) and recently productive channels revisited. Fixed-channel mode (`sdkconfig.fixed6`, `sdkconfig.channel149` overlays) is for diagnostics. The driver must additionally accept the country and channels; country US is a development setting. DFS channels and channel 14 are deliberately excluded. Hopping is verified on hardware (about 12 h soak: 173,815 channel switches, 0 tuning errors, no resets) and was the build used for the 2026-11-02 Remote ID field test.
- Tune once, read back channel and band, enable callback only after success. An initialization failure emits an error, disables/stops Wi-Fi and stays in `stopped_error`; no automatic radio reconfiguration. A stalled decode task is separately protected by a 10-second panic/reboot watchdog.
- Wi-Fi callback copies at most 1024 bytes into a zero-wait bounded FreeRTOS queue (default eight slots, configurable 2–32). The frame length is C5 MAC-v3 `sig_len - 4`: on hardware `sig_len` includes a 4-byte FCS slot (not a valid CRC, so it is discarded unchecked) and `dump_len` is `sig_len + 4`, despite its IDF v5.5.4 header comment saying it excludes the FCS. `dump_len` is used only as a consistency check; the eight-bit RX channel and RSSI are copied from metadata. No JSON, decoding, heap allocation, or printing in the callback. The consumer drains in bounded slices (at most 32 packets or 5 ms) and yields only at a slice bound; USB output runs in a separate bounded task.
- Stateless decoder: bounds-checked Beacon IEs and NAN attributes, compatible ODID versions 0/1/2, bounded declared pack size/count with zero padding, no nested pack, safe reserved/future member skipping and per-known-member decode checks. No cross-packet ID/location cache. Vendored [OpenDroneID provenance/license](components/odid/UPSTREAM.md).
- Emit an observation only for exactly one supported textual Basic ID (serial number or CAA registration) and valid nonzero aircraft lat/lon from the **same pack**. Binary IDs, ambiguous IDs, partial packs and invalid aircraft locations are held, not guessed. Operator location and altitude validity are independent. Invalid/unavailable operator data cannot replace the aircraft position.
- Radio delivery, candidate recognition, successful pack/message decode, semantic emission and losses have separate counters. Neither `monitoring` nor periodic status proves RF reception. Inspect increasing RF counters, then decode counters, then independently verified aircraft data.

## Build without hardware

Source acquisition and tool installation do not require a board. Install distribution prerequisites per the [official v5.5.4 guide](https://docs.espressif.com/projects/esp-idf/en/v5.5.4/esp32c5/get-started/linux-macos-setup.html), then:

```bash
mkdir -p ~/esp
git clone --branch v5.5.4 --depth 1 --recursive --shallow-submodules \
  https://github.com/espressif/esp-idf.git ~/esp/esp-idf-v5.5.4
cd ~/esp/esp-idf-v5.5.4
./install.sh esp32c5
. ./export.sh
cd ~/Projects/OUI-TAK/firmware/esp32-c5-receiver
idf.py -B build build
idf.py -B build size
```

`IDF_TARGET` defaults to `esp32c5` and other targets are rejected. No Arduino BSP is used. Board defaults select hardware USB Serial/JTAG console, 8-MB flash with conservative DIO/40-MHz operation, a 12-KB main stack, no PSRAM dependency, Bluetooth disabled, and no automatic sleep. LEDs/BOOT/USB pins are not repurposed. Confirm the delivered memory/antenna variant before any later flashing.

If CMake/Ninja are missing after activation, install them **inside the activated IDF environment**, not the system interpreter:

```bash
python -m pip install cmake==4.4.4 ninja==1.13.2
```

Configure a fixed channel and country via `idf.py menuconfig` → **OUI-TAK C5 receiver**. Menuconfig and build operate on files only. Changes to `sdkconfig.defaults` do not override an existing `sdkconfig`; use a fresh configuration for reproducible variants.

Fresh default/channel-6 build (shell must be in project directory with IDF activated):

```bash
idf.py -B build-verify -D SDKCONFIG="$PWD/build-verify/sdkconfig" build
```

Fresh channel-149 build, only for a region where it is permitted:

```bash
idf.py -B build-5g -D SDKCONFIG="$PWD/build-5g/sdkconfig" \
  -D 'SDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.channel149' build
```

Choose unused output directories for truly fresh builds. Generated configs/builds are ignored by Git. Building does not run `flash` or `monitor`. See [BUILD-RESULTS.md](BUILD-RESULTS.md) for executed results.

## Offline tests

From the project directory, using Python 3 and a host C compiler:

```bash
python3 -m unittest discover -s tests -p 'test_receiver.py' -v
```

The suite compiles the actual portable parser/JSON code and pinned decoder with `-Wall -Wextra -Werror`. It uses synthetic byte fixtures, never a serial device or RF API. A separate ASan/UBSan executable runs every fixture prefix and every one-byte mutation. Leak detection is disabled for sandbox compatibility; address and undefined-behavior checks halt on error.

Coverage includes two transports/two bands, malformed lengths, nested/unknown-version messages, missing/unsupported/duplicate IDs, missing Location/System, independent operator validity, unavailable altitude/time, output bounds/escaping, no cross-packet state, and versioned status/hello/error serialization. FreeRTOS queue saturation, USB backpressure, initialization return codes and on-air passivity still require hardware bring-up; host fixtures do not prove driver behavior.

See [hardware checklist](HARDWARE-BRINGUP.md) before connecting this project to hardware. No hardware commands were executed for C5-0.1.

## Standalone SUSPECTRUM operation

Power-on initializes fixed-channel passive monitoring without a laptop, USB handshake, dashboard, or TAK Server. Monitoring continues indefinitely while powered. Host Start/Stop only owns laptop serial readers and forwarding; there are no receiver control commands. USB output runs in a separate bounded task/queue, so disconnected or slow host output cannot block the receive/decode loop. Delivery is lossy; `output_errors` covers queue/format/write failures. Every JSON record reports `uptime_us` and numeric boot `reset_reason`. The task watchdog reboots a stalled decode task; configuration errors fail closed without factory restore.

See [operating model and validation](../../docs/DRONK-OPERATING-MODEL.md). Power continuity when unplugging USB depends on independent board power. Standalone reception, host attach/detach, sustained saturation, watchdog recovery and 5-GHz reception must still be validated on actual hardware; no flashing was performed.

## C5 preflight hardening

See [preflight report](../../docs/C5-PREFLIGHT-HARDENING.md) for protocol-version support, legacy field safeguards, new diagnostic counters, batch draining and measured host-model throughput. The queue consumer no longer sleeps per packet: it drains at most 32 packets or 5 ms, yielding only at a slice bound. The USB task, autonomous startup and passive fixed-channel APIs remain independent. Protocol fields are additive; old aggregate counters are retained and no dashboard features were changed.
