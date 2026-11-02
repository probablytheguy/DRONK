# Milestone 0.5 — source-only follow-up

Date: 2026-10-23. No serial device was opened, no ESP32 was reset, no commands were sent to it, no firmware was flashed, and no production Android files were changed in this follow-up. No GitHub writes were made. Only /tmp research artifacts and public upstream source history were updated.

## Conclusions

1. Unified boot code really attempts to restore persistent Wi-Fi settings to defaults. It is not merely a cosmetic message, but the unconditional log does not prove those API calls succeeded or that nondefault settings existed.
2. USB line states can reset the chip. The application then runs its ordinary boot-time Wi-Fi cleanup. No direct USB-connection-to-NVS-delete handler was found in the inspected Sky Spy path.
3. The previous DTR=false/RTS=false pyserial open pattern is not sufficient to guarantee a passive open on Linux. Source exposes an intermediate reset-capable state.
4. The legacy malformed scanning line matches pre-April-23-2026 Unified source. The installed commit remains unknown; the v2.0 banner is not a precise revision identifier.
5. A tolerant byte/JSON decoder can be designed, but the legacy schema cannot by itself establish all semantics required by OUI-TAK's validated observation boundary.

## Existing evidence and preservation

Existing raw sample SHA-256: `a84866ea99f2e051f4c8a1f283196cdbd2ff90d32729bc2de26c5bc6c1b1fad2`.
The raw sample and capture metadata were not modified. The prior report and procedure remain for audit.
OUI-TAK still has HEAD `779c2db99708966f1b28ffed158effd06f8c5cbd` and only the existing untracked `gradle/gradle-daemon-jvm.properties`; no tracked diff.

Correction to the earlier RESULTS.md: the capture script did not explicitly flush input, but pyserial 3.5 `Serial.open()` internally calls `_reset_input_buffer()`, which calls `tcflush(TCIFLUSH)`. Thus the statement that no input buffer was flushed was too strong. Likewise, zero serial payload bytes does not mean zero USB control requests. Opening and configuring a TTY can issue control transfers before the capture loop starts.

## Reset mechanism: fact versus proposed causal trace

### Facts supported by source

Espressif's ESP32-S3 Technical Reference Manual v1.8, section 33.4.2, Tables 33.4-3 and 33.4-4 (printed pages 1253–1254), documents hardware USB Serial/JTAG DTR/RTS reset sequences. DTR=0 with RTS=1 is a reset state. Normal flash-boot reset sequence is clear DTR, clear RTS, set RTS, clear RTS. Download-mode entry is a different sequence involving DTR. A reset is not a flash erase operation.

Reference: https://documentation.espressif.com/esp32-s3_technical_reference_manual_en.pdf
Saved PDF/text: `references/esp32-s3-trm.pdf`, `references/esp32-s3-trm.txt`.

Linux CDC ACM source v6.12:
- `acm_port_dtr_rts()` at line 665 sends DTR|RTS when active and zero when inactive.
- `acm_tty_tiocmset()` at line 941 updates one set/clear mask while preserving the other line's current state.
- `acm_set_control()` at line 137 issues USB SET_CONTROL_LINE_STATE.

Reference: https://github.com/torvalds/linux/blob/v6.12/drivers/usb/class/cdc-acm.c
Saved: `references/cdc-acm-v6.12.c`. This is a reference kernel version, not a claim that the capture host runs exactly v6.12 or has identical behavior.

The actual installed pyserial 3.5 implementation used by capture is available at `/tmp/ouitak05-serial-env/lib/python3.14/site-packages/serial/serialposix.py`:
- `open()` first calls `os.open()`, then reconfigures the TTY, then applies DTR, then RTS in separate calls (lines 322–339).
- `_update_dtr_state()` and `_update_rts_state()` each issue separate TIOCMBIC/TIOCMBIS operations (lines 703–715).
- Setting properties before open stores desired values; it cannot suppress all driver actions during the preceding `os.open()`.

### Strong hypothesis, not a captured control trace

With the driver's initial DTR/RTS assertion, clearing DTR before clearing RTS can produce:

| Stage | DTR | RTS | Meaning |
|---|---:|---:|---|
| Driver activates port | 1 | 1 | Both asserted |
| pyserial applies DTR=false | 0 | 1 | Reset-capable state |
| pyserial applies RTS=false | 0 | 0 | Both cleared; normal execution resumes |

This explains why the previous pattern can reset even though both requested final values are false. The recorded `rst:0x15 (USB_UART_CHIP_RESET)` and immediate boot output are consistent with it. We did not record SET_CONTROL_LINE_STATE packets, kernel open timing, or the initial line state, so exact causation is not proven. Buffered prior boot output or another actor cannot be completely excluded from the saved bytes alone. No physical USB disconnect is required for a CPU reset; absence of kernel re-enumeration does not disprove it.

Simply changing the order of property assignments before `open()` will not change pyserial's internal order. Opening read-only, using `cat`, disabling flow control, or setting HUPCL after open also does not prove absence of open-time controls. Avoid retries, 1200-baud touch, RTS recovery pulses, modem-state probes through a new open, and baud sweeps on this target.

## Configuration erasure: operation, scope, and uncertainty

Current Unified revision `6e5e2829540925ff1ab5fa6d0d365378818ed56e`, `src/main.cpp:504–520`, runs this sequence unconditionally inside `setup()` before mode selection:

```
WiFi.persistent(false);
WiFi.mode(WIFI_AP_STA);
delay(100);
esp_wifi_restore();
WiFi.softAPdisconnect(true);
WiFi.disconnect(true, true);
WiFi.mode(WIFI_OFF);
delay(100);
// prints factory-reset-complete message without checking return codes
```

The original Unified commit `fbcf188e02e70e98f0f95fe9df0a1a202ccae842` and the last pre-fix revision `ce6e231e7ed2ef11f00d8dc12c1c2cfdc13419a9` already contain the restore/disconnect sequence; their boot path lacks the later persistent(false) addition. This is not exclusively a recent upstream behavior.

Espressif documents `esp_wifi_restore()` as restoring persistent Wi-Fi stack settings, including config, protocol, bandwidth and mode. It can fail if the driver is not initialized. Arduino-ESP32 2.0.9 `WiFiSTAClass::disconnect(wifioff, eraseap)` clears the STA config via esp_wifi_set_config when eraseap=true; persistence details depend on Wi-Fi storage mode and actual core version. WiFi.persistent(false) is not an undo or a guarantee that an explicit esp_wifi_restore() is harmless.

References:
- https://github.com/colonelpanichacks/oui-spy-unified-blue/blob/6e5e2829540925ff1ab5fa6d0d365378818ed56e/src/main.cpp#L504
- https://docs.espressif.com/projects/esp-idf/en/v4.4.4/esp32s3/api-reference/network/esp_wifi.html#_CPPv416esp_wifi_restorev
- https://github.com/espressif/arduino-esp32/blob/2.0.9/libraries/WiFi/src/WiFiSTA.cpp#L320

Scope distinction: ordinary boot restores the Wi-Fi driver's settings. It does not call nvs_flash_erase() in this path, and does not explicitly erase every Preferences namespace. Current source has a separate selector HTTP `/nuke_nvs` handler that calls nvs_flash_erase(), then reinitializes NVS and reboots; it was not invoked. Other selector handlers can change mode/AP settings; none was invoked. The saved log still reads stored mode 5 after Wi-Fi cleanup, consistent with a surviving `unified-mode` namespace.

The startup success message is unconditional. Confirmed: real restore calls exist in matching source families and the device printed that message. Not confirmed: API return codes, actual before/after NVS contents, presence of prior credentials, or exact installed source. It is accurate to say “the boot path attempts persistent Wi-Fi reset,” not “all device configuration was proven erased.”

## USB connection and CDC initialization

| Trigger | Source-supported result |
|---|---|
| CDC DTR/RTS reset sequence | Can reset ESP32-S3 through hardware USB Serial/JTAG; next application boot attempts Wi-Fi restore |
| USB enumeration/serial open alone | No direct application deletion handler found; host control transfers can indirectly trigger reset |
| Serial.begin(115200) | Initializes serial; no Wi-Fi deletion call in inspected implementations |
| USB bus reset event | Not equivalent to a CPU reset or NVS erase; HWCDC treats bus events separately |
| Power cycle or other CPU reboot | Runs the same unconditional Unified setup cleanup |
| 1200-baud TinyUSB touch | Arduino USBCDC supports a separate reboot mechanism; not established as this native USB Serial/JTAG path |

`HWCDC::begin()` in Arduino-ESP32 2.0.9 sets buffers, USB Serial/JTAG interrupts and FIFO state, not NVS. Saved `references/HWCDC-2.0.9.cpp`.
The distinct TinyUSB `USBCDC.cpp` implements line-state reboot recognition and 1200-baud behavior. Do not confuse it with fixed-function hardware USB Serial/JTAG based only on the word CDC. Actual dependency versions/build flags are not embedded in the captured banner; platformio.ini uses a version range, so these core sources are illustrative, not an exact installed-build proof.

References:
- https://github.com/espressif/arduino-esp32/blob/2.0.9/cores/esp32/HWCDC.cpp#L171
- https://github.com/espressif/arduino-esp32/blob/2.0.9/cores/esp32/USBCDC.cpp#L179

## Revision comparison

Fetched the complete reachable Unified history through pinned HEAD `6e5e282` into the existing /tmp checkout. Examined 110 reachable commits; `revision-matrix.json` records flags for every commit. Forty commits have both the v2.0 banner and legacy malformed status text, from initial `fbcf188` (2026-02-06) through `ce6e231` (2026-04-17). This is a compatibility set within fetched main history, not an identification of the binary. Forks, local edits, mixed source and published binary lag remain possible.

| Source family/revision | Status | Relevant behavior |
|---|---|---|
| Unified fbcf188 through ce6e231 | Malformed legacy scanning line matches capture | v2.0 banner, mode 5, boot Wi-Fi restore; older BLE decoder can emit stale/zero data on unsupported messages |
| Unified 5d29b146c4cca5579ff98e53069e3232cd096587, 2026-04-23 | Replaced with valid status=scanning JSON | BLE decode validity checks, pack decoding, slot clearing improved; same detection output keys |
| Unified 404abbe, 2026-04-24 | Valid scanning JSON | Suppresses benign BLE parse logs |
| Unified 0fe8cb8, 2026-05-10 | Valid scanning JSON | Raises MAX_UAVS to 32 |
| Unified 8bbfdfb, 2026-08-24 | Valid scanning JSON | Expands AP-persistence cleanup/preamble; boot restore predates this commit |
| Unified 6e5e282, 2026-10-11 | Valid scanning JSON | Current inspected HEAD, additional modes; banner remains v2.0 |
| Standalone Sky-Spy 1930c9b731f9d01c161753e931377921bb9ba267 | Valid scanning JSON in S3 source | Same eight S3 detection keys; standalone identity does not match captured Unified boot banner |

The April 23 fix directly changes the exact captured string, and also fixes BLE callbacks that emitted records without useful decoded fields. An old-looking status therefore raises an additional data-validity concern; it does not prove the installed firmware contains every old bug. Inspecting source cannot authenticate actual detection data that was never captured.

Source diff: https://github.com/colonelpanichacks/oui-spy-unified-blue/commit/5d29b146c4cca5579ff98e53069e3232cd096587

## Safe future observation and recommended next test

There is no validated zero-reset TTY-open procedure for this target yet. Do not repeat the previous open recipe on the OUI-SPY as a safety test.

Safest immediate target observation is host-side passive USB traffic monitoring (usbmon if already available) without opening its TTY, rebinding drivers, changing line settings, or starting a reader. It only observes traffic already occurring; with no active bulk reader it may yield no serial payload, so it cannot promise a detection sample. Existing capture replay remains completely offline and safest for parser development.

Recommended next hardware test: use a spare ESP32-S3 USB Serial/JTAG device, with disposable configuration and preferably matching firmware/kernel setup. Under usbmon and syscall tracing, verify open/close control transfers and boot/reset evidence for the full reader lifecycle, including error handling. Do not use the configured OUI-SPY to test reset avoidance. No such test was performed here.

For a genuinely receive-only target path, evaluate a pre-reviewed USB bulk-IN reader that never opens a TTY and never issues SET_CONTROL_LINE_STATE, SET_LINE_CODING, OUT data, device reset or configuration changes. However, detaching CDC ACM, claiming interfaces and cleanup may themselves have side effects, and the kernel driver may already own the endpoint. This remains an experimental option requiring spare-device validation of setup, steady read, errors and close; it is not a ready-to-run safe command.

A high-impedance UART/logic-analyzer receive-only tap is another physical option only after schematic and actual Serial routing are confirmed. This firmware's native USB stream must not be assumed to appear on UART pins. Inserting an inline USB analyzer on a live connection can itself disconnect/reset the device, so it is not an immediate zero-disruption remedy.

Acceptance gates before returning to the target: no CPU boot/reset evidence; no reset-capable line transitions or unsolicited writes across the entire lifecycle; no background modem manager/auto-reader interference; bounded raw recording; abort without reset/reconnect on errors; settings preservation demonstrated on the disposable setup. Only after that, observe a controlled known Remote ID source near the target to capture real detections without issuing any command to the OUI-SPY. Exact firmware semantics still require comparison against that sample.

Parser design is in `PARSER-DESIGN.md`. No parser implementation or production integration was performed.
