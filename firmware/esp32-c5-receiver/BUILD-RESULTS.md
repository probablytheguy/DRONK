# C5-0.1 implementation validation — 2026-10-28

## Scope

Two fresh firmware build trees completed successfully for **esp32c5**, fixed channels **6** and **149**. No device enumeration/open, flashing, monitoring, hardware changes or RF test transmissions were performed. This establishes compile/link/configuration compatibility with the pinned SDK, **not** board boot, tuning, RF reception, or Avata compatibility. Every [hardware bring-up gate](HARDWARE-BRINGUP.md) remains open.

## Provisioned development environment

- ESP-IDF tag `v5.5.4`, commit `735507283d5b2f9fb363a1901172dbd9e847945d`, source at `~/esp/esp-idf-v5.5.4`, recursive pinned submodules.
- Installed with `./install.sh esp32c5`; RISC-V GCC toolchain `esp-14.2.0_20260121`.
- Version-specific Python environment: `~/.espressif/python_env/idf5.5_py3.14_env`; Python 3.14.8. Compiler/debugger downloads use the standard user-level `~/.espressif` tool cache. No system package installation or root use.
- CMake 4.4.4 and Ninja 1.13.2 installed using pip inside that IDF environment, because neither was initially on PATH.
- Vendored OpenDroneID commit `849c03adbe0cdd68997a462fafab397edcfe53f3`; source/header/license byte-for-byte matches verified against the pinned checkout. Two upstream header lines retain original trailing whitespace; source was intentionally not reformatted.

Initial compile identified an unused include with a missing component dependency, which was removed. An initial CMake version guard referenced a non-exported variable; it now uses IDF's published environment version. These were implementation fixes, not SDK/board limitations. Subsequent fresh builds below passed without compiler warnings in the captured logs.

## Clean builds executed

After activating the SDK, from `firmware/esp32-c5-receiver`, with these build directories/config files previously absent:

```bash
idf.py -B build-verify -D SDKCONFIG="$PWD/build-verify/sdkconfig" build
idf.py -B build-5g -D SDKCONFIG="$PWD/build-5g/sdkconfig" \
  -D 'SDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.channel149' build
idf.py -B build-verify size
```

Both generated application, bootloader and partition-table binaries successfully. Generated configurations confirm C5 target, USB Serial/JTAG console, 8-MB flash, 240-MHz CPU, 12288-byte main stack, Bluetooth and PSRAM disabled; channel is respectively 6 and 149. No board is necessary for these commands. IDF prints suggested flashing commands after building; none was executed.

Both application `.bin` files are **693568 bytes** (`0xa9540`), leaving 34% of the default 1-MB application partition free. The board configuration reserves 8-MB flash but does not require a larger app partition. Bootloader size is `0x3950`.

| Locally generated artifact | SHA-256 |
| --- | --- |
| `build-verify/ouitak_c5_receiver.bin` | `6e37e0ce360193c6caefdb5eaeaba03b14b8e1f04a636ffc79de374ee7a10905` |
| `build-5g/ouitak_c5_receiver.bin` | `eb8ed35ed3a3f15f487e5a2ee065dd0b7c82f588e8b1f453ee1925ad212f90f9` |

These identify these local builds, not a reproducible-build promise: SDK application descriptors can contain build metadata. Binaries/configs remain ignored local artifacts; source and defaults are committed.

`idf.py size` reports HP SRAM static use **127114 bytes** (39.61% of its reported available region), including 88074 text, 24056 BSS and 14984 data. LP SRAM static use: 24 bytes. This is not runtime heap headroom: Wi-Fi initialization, queues and task stacks allocate additional memory. Runtime heap/stack and queue-overload validation remain pending.

Local build logs: `/tmp/ouitak-c5-build-verify.log`, `/tmp/ouitak-c5-build-5g.log`, `/tmp/ouitak-c5-size.log`. These are diagnostic artifacts, not committed dependencies.

## Offline verification

```bash
python3 -m unittest discover -s tests -p 'test_receiver.py' -v
```

**21 tests passed.** Production parser/JSON and pinned decoder compiled on the host with `-Wall -Wextra -Werror`. Tests cover valid Beacon/NAN, both frequency maps, malformed/truncated/oversize frames, unsupported versions/nesting/layouts, unrelated traffic, duplicate/unsupported/empty IDs, absent Location/System, independent invalid/unavailable operator position, time/altitude sentinels, bounded JSON and escaping, status counters, and no packet-to-packet state leakage.

ASan/UBSan corpus passed: every prefix and every one-byte mutation of the three committed synthetic fixtures (72192 mutations plus 285 prefixes). LeakSanitizer disabled for sandbox compatibility; address/UB errors halt. This is a bounded mutation corpus, not exhaustive fuzzing or proof that all wireless frames are safe. No captured Avata fixtures exist yet.

One initial test expected a reserved Basic ID type to make the upstream decoder fail, although upstream preserves that type; the case now correctly tests unknown message type while separate tests verify unsupported IDs never emit observations. A timestamp-sentinel test offset was corrected against the upstream packed layout. Final tests above pass.

No existing application/bridge/tracking files were changed. The legacy Linux adapter's 10 offline tests also passed after implementation. Live serial, RF, Android instrumentation and TAK forwarding were not run.

## Outstanding gates

Board boot/USB framing and backpressure; actual flash/antenna revision; successful tune/readback and real management delivery on 2.4/5 GHz; verified C5 metadata/FCS behavior; independent Avata packet comparison; initialization failure handling on hardware; FreeRTOS queue/heap/stack/watchdog stress; external confirmation of receive-only RF behavior. BLE, hopping and host protocol integration are explicitly outside C5-0.1.
