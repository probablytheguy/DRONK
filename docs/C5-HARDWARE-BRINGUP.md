# C5 hardware bring-up: first SUSPECTRUM test

Status: **steps 1–6 run on hardware 2026-10-30; step 6 passed 2026-11-01 after `aa52133`; step 7 passed for reception on 2026-11-02 (indoor, no GPS fix); steps 8 (position), 9 (tracking) and 10 (TAK) not verified.** See [session results](#session-results-2026-10-30) and [2026-11-02 field results](#field-results-2026-11-02-first-real-remote-id). Originally written for security Phase 1 (`e72b06e`) and Phase 1.1 code (`1786cc1`). The authoritative gate list is [`firmware/esp32-c5-receiver/HARDWARE-BRINGUP.md`](../firmware/esp32-c5-receiver/HARDWARE-BRINGUP.md); this document is the step-by-step procedure for the first session. Record every result in a dated log outside the repository.

> **Unverified until this test:** that the ESP32-C5 delivers **5 GHz** management frames to the promiscuous callback in NULL mode, and that SUSPECTRUM can receive and decode the **original DJI Avata's** Remote ID broadcasts (band, channel, Beacon vs NAN transport, ODID version). Builds and offline tests don't prove either. Ordinary 5 GHz Wi-Fi traffic proves reception only, not Remote ID compatibility.

## Session results 2026-10-30

First physical session, CAPTURE only, no TAK. Firmware fix `6ee9e41`. Identifiers (MAC, by-id serial) are kept in the private log, not here.

| Step | Result |
| --- | --- |
| 1–2 USB / identity | **Pass.** One `usb-Espressif_USB_JTAG_serial_debug_unit_<MAC>-if00` device; esptool reports ESP32-C5 chip revision v1.0, 8 MB flash. ESP-IDF 5.5.4. |
| 3 Flash | **Pass with manual download mode.** `idf.py ... flash` and plain esptool fail with `Write timeout` while the app runs. After BOOT+RESET, `esptool ... --before no_reset --after no_reset --no-stub write_flash @flash_args` verified all hashes. After flashing, tap RESET alone, or the board stays in download mode. |
| 4 Telemetry | **Pass with a read-only raw reader**: hello, `monitoring`, status every 5 s. DRONK Control connected but showed `awaiting_data` (operator report); end-to-end host telemetry still needs checking. |
| 5 Channel 6 | **Pass after the fix.** Before: every frame malformed. The callback used `dump_len`, which on this board is `sig_len + 4`. Using `sig_len` still left about 83% parser-malformed: 4 trailing bytes broke the beacon IE walk. A raw beacon capture showed the MPDU ends at `sig_len - 4`; the trailing bytes were **not** a valid CRC-32/FCS. After `6ee9e41`: 1,074 RF frames in 45.2 s, 0 malformed, 0 queue/oversize drops, 0 `rx_reject`, 0 candidates (no known transmitter), no unexpected resets. 30 firmware tests passed, including ASan/UBSan. |
| 6 Channel 149 | **Pass on 2026-11-01 after `aa52133`.** On 2026-10-30: channel 149 / 5745 MHz, `monitoring`, 23 RF frames in 60.8 s, all accepted by the callback, but 19 parsed as malformed. A raw-frame diagnostic on 2026-11-01 showed the malformed frames were VHT compressed beamforming reports (Action No Ack, subtype 14, OFDM) with nonzero fragment bits, rejected by a header check that ran before the subtype check. Their declared size matched `sig_len - 4` exactly, so the frame length is right for OFDM too. After `aa52133`: 28 RF frames in 61.0 s, 0 malformed, 0 errors, 0 drops, 0 `rx_reject`, 0 candidates (no Remote ID source), no unexpected resets. 31 firmware tests passed, including ASan/UBSan. |
| 7–10 | Not run. Avata Remote ID, DRONK tracking, and TAK forwarding remain untested. |

Resume from [START HERE SUNDAY](DRONK-RECOVERY.md#start-here-sunday).

## Field results 2026-11-02: first real Remote ID

First real, nonsynthetic Remote ID received by SUSPECTRUM. DRONK Control in CAPTURE, TAK forwarding `DISABLED` (0 attempts, 0 writes). Aircraft: original DJI Avata with Goggles V2 and Motion Controller 2, **indoors**, phone connected to the goggles, **no GPS fix**. Receiver: the hopping build flashed for Phase 2 (`66eab97b…`); the session ID was unchanged since that flash, but the hello line with the firmware version was outside the saved log window, so the build is inferred, not re-read. The aircraft serial and transmitter MAC are kept in the private log only.

| Item | Result |
| --- | --- |
| Receptions | **66** `reception` records between 13:39:17 and 13:40:38 UTC (81 s), `synthetic: false`. Receiver counters agree: `candidate_frames` 66, `decoded_packs` 66, `decoded_messages` 198, `rid_receptions` 66, `decode_failures` 0, `unsupported_versions` 0. |
| Band / channel | 5745 MHz, channel 149, as reported by the receiver for the channel it was tuned to. All 66 decodes were on 149; the scan row for 149 went hot. 153–165 had no decodes. |
| Transport | Wi-Fi Beacon. One transmitter MAC throughout. |
| OpenDroneID | Protocol version 2. Each pack had Basic ID, Location and System (3 messages). Basic ID type 1 (serial number), UA type 2, one textual 20-character serial. |
| Position | `aircraft_location: unavailable`, `reason: no_valid_position`, so no `observation` or track was created (as designed in Phase 2). |
| RSSI | −60 to −44 dBm. |
| Host | 0 rejected, 0 duplicates, 0 transport errors. Detection panel showed DRONE DETECTED, 0 tracked / 1 not trackable. Track history export was **empty**, which is what prompted the detection-record change below. |
| Receiver soak | Same boot since the Phase 2 flash: about 12 h uptime (43,454 s), reset reason 1 (power-on), 173,815 channel switches, 0 tune errors, worst switch 2.238 ms, 186 transition drops, 458,458 RF frames, 0 queue/oversize drops. |
| Open questions | `malformed_frames` 332 (0.07% of RF frames; 0 at the 669 s mark of the same boot), not yet explained. `usb_output_drops` 8,469 accumulated over the hours with no host reading the port; not yet confirmed that none occurred while DRONK was connected. |

What this proves: SUSPECTRUM received and decoded the Avata's real Wi-Fi Beacon Remote ID on 5 GHz channel 149, and the host accepted it without rejections. What it does not prove: the channel/frequency is the receiver's own tuned-channel report, not an independent RF measurement (no reference receiver capture was saved); no valid aircraft coordinates were decoded; nothing was tracked or forwarded. Steps 8 (position), 9 (tracking) and 10 (TAK) remain open.

How this log was kept: DRONK had no capture file yet. The records above come from the dashboard's in-memory log (the last 1,000 events), copied from the running console after the test and stored privately outside the repository. Raw receiver lines from the session were not kept; only the bridge's validated events and the receiver telemetry. Since Phase 0 (below), every session writes its own append-only capture, including raw lines.

**Reported tuning vs. verified RF.** "5745 MHz / channel 149" is the receiver's report of the channel it had tuned when it decoded each pack (`frequency_mhz` in its own records). That is strong evidence, but it is not an independent RF measurement: no reference receiver captured the Avata's beacon on 149 in this test. Treat the channel as receiver-reported until a reference capture confirms it.

Follow-up changes (software only, not yet run against live hardware):

- **Phase 0 capture** (`tools/dronk/CAPTURE.md`): each monitoring session now writes a new append-only JSONL file with raw receiver lines, validated Remote ID records with receiver `session_id`/`seq` provenance, and per-session diagnostics for USB output drops, malformed frames, queue drops and USB disconnects. That answers the two open questions above on the next run: the deltas show whether drops or malformed frames occur while DRONK is connected.
- **Phase 0 hardware validation, 2026-11-02** (same board and boot, CAPTURE, TAK `DISABLED`, no aircraft present). Two sessions on the restarted console: 3 min 0 s (`…141832.597Z-e7bba945`, 978 records) and 2 min 0 s (`…142433.042Z-d17ca33c`, 131 records). Both files are complete (`capture_end` reason `requested`), consistent, 0 malformed lines, and still read back complete after the dashboard was restarted; the second names the first as `previous_capture`. Raw lines, telemetry, USB events and diagnostics were all recorded. **No Remote ID was present, so the decoded-reception path was not exercised on hardware in these sessions**; it is covered by the pseudo-terminal tests through the real bridge.
  - **USB output drops during active capture: 0** in both sessions (8,915 throughout the first; delta 0 in the second). The drops seen at connect (+446, then +34) accrued **between** sessions while no host was reading: the receiver delivered two stale status lines (uptime 37 min and 3 min old) followed by exactly one empty resync line per dropped record. The first session's diagnostics were baselined on the stale line and over-reported +446 drops and +32,062 RF frames; fixed by detecting the uptime jump (re-run confirmed: `rebaselined: stale_buffered_status`, delta 0).
  - **Malformed frames during active capture: +1 in 3 min, +5 in 2 min** (5 of 1,306 RF frames in the second session, about 0.4%). Queue and oversize drops 0. The cause is still open; the firmware counts malformed frames but doesn't log them.
  - **Requested stop counted as a USB disconnect** in the first session (`disconnects: 1`). Fixed: the host's own close is recorded as `closed_by_host`; second session `usb_disconnects` 0. The dashboard also now shows `CLOSED` after Stop instead of a stale `RECORDING`.
- DRONK Control now keeps an aircraft detection record for a reception with a usable Basic ID even without position (`DETECTED_NO_POSITION`), and includes it in the CSV/JSON export. A replay of this session's 66 receptions (identifiers replaced) is a regression test: `tools/dronk/tests/fixtures/field-2026-11-02-avata-receptions.jsonl`.

## Equipment

| Item | Notes |
| --- | --- |
| Seeed Studio XIAO ESP32-C5 | Record the module and PCB revision, flash size and MAC. Confirm the antenna connector arrangement physically; it's unverified here. |
| External 2.4 GHz and 5.8 GHz antennas | Record which antenna is attached for each step. Attach the antenna before powering for RF tests. |
| Omarchy Linux laptop | Verify device-group access and no competing serial-port users; ESP-IDF v5.5.4 at `~/esp/esp-idf-v5.5.4`. |
| SUSPECTRUM firmware | `firmware/esp32-c5-receiver`, app 0.2.1 / `C5-0.2-preflight`, built per [DRONK-RECOVERY.md](DRONK-RECOVERY.md) §6. Prebuild channel 6 and 149 images; optionally 153/157/161/165. |
| Original DJI Avata | Verify Remote ID availability, firmware/app requirements and GPS state for the actual unit/region. Phone/goggles and 5-GHz/channel-149 assumptions are not hardware-verified by this project. Fly only where and when legally permitted. |
| TAK Server and TAK Aware | Existing server, configured locally only (never in the repo). Forwarding is the **last** step. |
| Optional reference receiver | A monitor-mode Wi-Fi adapter with `tcpdump`/Wireshark or `unix_rid_capture`, used to establish ground truth independently. Strongly recommended for steps 5–8. |

**Safety rules:** passive reception only. No spoofing, transmission tests or probing. Run each gate manually and stop on a failure; these blocks are not an unattended script. **Unplug the OUI-SPY ESP32-S3** before step 1: both boards enumerate as `usb-Espressif_USB_JTAG_serial_debug_unit_<MAC>-if00`, so having both connected risks flashing the wrong one. Keep the TAK transport disabled until step 10.

## Test sequence

Each step lists what to do, the pass criteria, and what failure looks like. Stop at the first failure and use the troubleshooting section.

### 1. USB device detection

```bash
sudo dmesg -w            # in a second terminal, then plug in the C5
ls -l /dev/serial/by-id/
```

- **Pass:** a new `usb-Espressif_USB_JTAG_serial_debug_unit_<MAC>-if00` link pointing at `/dev/ttyACM*`; `dmesg` shows `cdc_acm`. Record the by-id path; it's used by every later step.
- **Fail:** no enumeration, or a charge-only cable. Try another cable or port. If it still fails, hold BOOT while plugging in, then retry.

### 2. Confirm hardware and flash configuration

The S3 must be unplugged and the C5 must be the identified new by-id entry. In the same Bash terminal, select its actual path; do not paste a placeholder or infer the chip from its USB name.

```bash
read -r -p 'Paste the verified C5 by-id path: ' C5_PORT
export C5_PORT
test -e "$C5_PORT"
source ~/esp/esp-idf-v5.5.4/export.sh
python -m esptool --chip esp32c5 --port "$C5_PORT" chip_id
python -m esptool --chip esp32c5 --port "$C5_PORT" flash_id
```

These identification commands open the device and may reset it. They are future manual hardware operations, not read-only host metadata checks. Do not proceed to flash until their results match the C5/8-MB gate.

- **Pass:** chip ESP32-C5 and 8 MB flash, matching `sdkconfig.defaults` (8 MB, DIO, 40 MHz).
- **Fail:** a different chip or flash size. **Stop.** Fix the board configuration in a reviewed change before flashing.

### 3. Flash the firmware (channel 6 image first)

Build first using [recovery section 6](DRONK-RECOVERY.md#6-firmware-build-commands-committed) in a fresh build directory; current firmware is `6ee9e41`. Only after step 2 passes:

```bash
cd ~/Projects/OUI-TAK/firmware/esp32-c5-receiver
sha256sum build-friday-ch6/ouitak_c5_receiver.bin
idf.py -B build-friday-ch6 -D SDKCONFIG="$PWD/build-friday-ch6/sdkconfig" -p "$C5_PORT" flash
```

No erase step is needed.

- **Pass:** "Hash of data verified" and a normal reset.
- **Fail:** sync or timeout errors. Enter download mode manually (hold BOOT, tap RESET), retry once, and record the error text. On 2026-10-30 automatic reset always failed with `Write timeout`; the esptool flash command that worked is in [START HERE SUNDAY](DRONK-RECOVERY.md#start-here-sunday). Afterwards, tap RESET alone to start the app.

### 4. Confirm autonomous scanning and serial telemetry

Use the maintained read-only 115200 8N1 host path rather than an unconfigured `cat`. Build the JVM bridge beforehand (`./gradlew --offline :linux-bridge:installDist`). In the same terminal where C5_PORT was set:

```bash
cd ~/Projects/OUI-TAK
python3 tools/dronk/command_center.py --mode CAPTURE --device "front=$C5_PORT"
```

Open `http://127.0.0.1:8765` and press **Start DRONK**. The console starts STOPPED; launch alone does not open the port. Monitoring then runs until **Stop DRONK**. For a timed diagnostic capture add `--duration 120`: each session stops after 120 s with `Timed capture complete`, and the console stays open. CAPTURE does not instantiate TAK forwarding, even when the private TOML contains TAK settings. Do not run a second reader or flash tool while this worker owns the port.

- **Pass:** receiver USB connected; CAPTURE banner; last-reported `monitoring`, channel 6 / 2437 MHz, changing uptime and firmware counters, session/reset reason. The hub validates protocol v1; the UI normalizes records, so raw hello flags need not be displayed. A late attach can miss hello without being a failure if valid status arrives.
- **Autonomy gate:** stop host monitoring, then start it again while the board remains powered. Record uptime/session/reset reason across both opens; TTY activation itself may reset this board. A new session after a deliberate power-cycle is expected. Never interpret a host Stop as receiver RF shutdown.
- **USB detach gate:** only after confirming the board's supported independent power arrangement, test data detach/reattach. Pulling its only USB power cannot demonstrate continued scanning. Actual watchdog recovery/reset/USB enumeration behavior remains a separate hardware gate; don't inject a firmware fault into this first production test.
- **Fail:** silence, `stopped_error`, repeated resets, or bridge rejection. Record the error/category and last counters privately. A running status proves configuration/loop progress, not RF reception.

### 5. Passive 2.4 GHz reception (channel 6)

Watch the `counters` in successive `status` records for 60 s in a location with ordinary Wi-Fi.

- **Pass:** `rf_frames` increases when independently confirmed management traffic is present on channel 6; `malformed_frames`, `queue_drops` and `usb_output_drops` stay near 0.
- **Fail:**
  - `rf_frames` doesn't move: check the antenna and channel, and confirm with the reference adapter that channel 6 has traffic.
  - `malformed_frames` rises with every frame: suspect a callback metadata mismatch (for example the channel field) and capture several `status` records for analysis.
  - `queue_drops` grows: the channel is saturated; record its rate.
- **RF emission check (optional, recommended):** the reference adapter should see **no** frames with the C5's MAC address.

### 6. Passive 5 GHz reception (channel 149, 5745 MHz)

Stop DRONK and exit the console first so the port is free. Use the prebuilt fixed-channel image only where permitted by the configured country policy and local conditions:

```bash
cd ~/Projects/OUI-TAK/firmware/esp32-c5-receiver
idf.py -B build-friday-ch149 -D SDKCONFIG="$PWD/build-friday-ch149/sdkconfig" \
  -D 'SDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.channel149' -p "$C5_PORT" flash
```

Then repeat the CAPTURE command from step 4. Country is US in this build; no automatic hopping or active scan is used. Channel acceptance still requires delivered-hardware verification.

- **Pass:** `status` shows channel 149 / 5745 MHz in `monitoring`, `rf_frames` rises when 5 GHz access points are nearby (confirm with the reference adapter), and `malformed_frames` stays near 0.
- **Fail:**
  - A tune error, or 0 frames while the reference adapter sees traffic on 149: try 157 or 161 images, swap to the 5.8 GHz antenna, and record the results.
  - If no 5 GHz management frames arrive on any channel, 5 GHz promiscuous reception is **not confirmed**. Stop the Avata 5 GHz plan and report it.
- **Reminder:** passing this step proves 5 GHz reception only, **not** Remote ID compatibility.

### 7. Attempt real Avata Remote ID reception

**Result 2026-11-02: reception passed** (indoors, no GPS): 66 decoded Wi-Fi Beacon packs on channel 149, ODID v2. See [field results](#field-results-2026-11-02-first-real-remote-id). An independent reference capture is still outstanding.

Use the unchanged Phase 2 hopping build (`66eab97b…`) as the baseline; don't change the scheduler or flash anything else before this test.

**What DJI and the FCC filing say** ([DJI Remote ID FAQ](https://support.dji.com/help/content?customId=en-us03400007747&spaceId=34&re=US&lang=en), FCC ID SS3-QF2W4K22):

- The original DJI Avata is listed as supporting Remote ID. DJI's check is a phone Wi-Fi scan for a network named `RID-` plus the 20-digit serial, so expect a **Wi-Fi Beacon**.
- **Broadcast needs the motors running.** DJI's broadcast conditions are "in flight (i.e. the propellers are turning)", and its Wi-Fi check starts by starting the motors. Powered on with motors off may show nothing. For a ground test, run the motors with propellers removed or otherwise made safe.
- **Location source with the Motion Controller.** The Motion Controller has no GNSS. DJI says to update the Goggles firmware and connect a phone or tablet with GNSS as the location source, with DJI Fly always allowed to read location. The FAQ doesn't name Goggles V2; confirm in DJI Fly or the goggles that Remote ID shows ready.
- **Pre-flight self-test.** The drone tests its Remote ID system before takeoff and can't take off if the test fails.
- **Band.** The FCC filing summaries for SS3-QF2W4K22 list transmitters only around 5.73–5.85 GHz, which points to channels 149, 153, 157, 161 or 165. The exact channel is **unconfirmed until measured**. The hopping build covers all five (149 as an anchor, the others in the sweep).

Procedure: start DRONK in CAPTURE first, run the motors as above, confirm the `RID-` network on a phone near the drone, and keep the reference adapter sweeping 5.8 GHz to record which channel carries the beacon. Start on the ground; take off only under legal conditions.

- **Pass:** `candidate_frames` rises, then `decoded_packs`.
- **Fail:**
  - `candidate_frames` stays 0 while `rf_frames` rises: wrong channel or transport. Find the Avata's Remote ID frames with the reference adapter, note the channel and whether it's a Beacon (vendor IE `FA:0B:BC`, type `0x0D`) or NAN, then reflash for that channel.
  - Candidates rise but `decoded_packs` doesn't: check `unsupported_versions`, `decode_failures`, `malformed_frames` and `skipped_messages`. Save the reference capture for offline analysis.

### 8. Validate OpenDroneID parsing

**2026-11-02:** Basic ID validated against the aircraft (serial number, type 1). Position not yet tested: no GPS fix indoors. Repeat outdoors with a fix.

Compare `observation` records against DJI Fly and the reference capture:

- **Pass:**
  - `basic_id.id_type` is 1 (serial) or 2 (CAA), and `value` matches the aircraft's broadcast ID.
  - `aircraft` is `valid` with coordinates matching the flight position within GPS error.
  - Altitude is reasonable; `odid_protocol_version` is recorded.
  - `operator` data is reported separately and never used as the aircraft position.
- **Fail:**
  - No `observation` despite decoded packs: there's no textual ID or no valid same-pack location. Expect 0,0 before a GPS fix.
  - Coordinates disagree: save frames and record the discrepancy.
- Save sanitized frames as future fixtures. Remove the aircraft serial and real locations first; never commit raw captures.

### 9. Verify DRONK receiver tracking (capture only, no forwarding)

Steps 4–8 already use the post-security host path. For an operator-controlled longer capture (no automatic duration limit), with the bridge built and C5_PORT still set:

```bash
cd ~/Projects/OUI-TAK
python3 tools/dronk/command_center.py --mode CAPTURE --device "front=$C5_PORT"
```

Open `http://127.0.0.1:8765` and manually press Start. Alternatively put the verified path under `[receivers]` in the private TOML; raw expected IDs stay private and public aliases can be configured there. No pre-Phase-1 command or private runtime default is needed.

- **Pass:**
  - Status CAPTURE; the receiver card shows connected, channel, uptime, reset reason and rising counters.
  - Host `rejected` = 0; observations appear in the log and export.
  - Stop closes the TTY and the console stays up.
- **Fail:**
  - `rejected` rises: compare the raw line from step 4 against the hub's validation rules in `DronkReceiverHub.kt` and record the field.
  - DISCONNECTED: check the TTY path and permissions.

### 10. Forward CoT and confirm markers in TAK Aware

Only after steps 1–9 pass and forwarding is separately authorized. Configure the TAK hostname, optional connection address and protected credential paths in the private TOML, never in Git. Review pilot-location policy before Start: new examples default off; the existing private opt-in was explicitly preserved. To keep pilot/takeoff location out of this first forwarding run while still retaining it in local decoding/logs/exports:

```bash
cd ~/Projects/OUI-TAK
python3 tools/dronk/command_center.py --mode LIVE --device "front=$C5_PORT" --no-forward-pilot-location
```

The CLI policy overrides the private opt-in for this run only. To intentionally forward valid broadcast pilot/takeoff data, use `--forward-pilot-location` or the reviewed private `[tak] forward_pilot_location = true`. A type-0 location is takeoff, not necessarily the current pilot. Neither setting changes aircraft coordinates or local decoding.

- **Pass:**
  - The TAK card shows the transport CONNECTED and `written` rising with `failed` = 0.
  - TAK Aware shows an `a-u-A` marker labelled with the aircraft ID that moves with the flight and goes stale about 60 s after the last observation.
- **Fail:**
  - Certificate or hostname errors: check the CA bundle, the TLS hostname and the system clock. Never disable verification.
  - Writes succeed but no marker: check the TAK Aware filters and server group, and confirm the server receives CoT.
- **Bounded forwarding:** serial ingestion is isolated from TAK I/O. The 64-entry FIFO drops oldest pending events on overflow; a 10-second deadline closes a stalled connection. Check TAK `queued`, `queue_drops`, `timeouts`, `stale_drops` and `shutdown_drops` in logs/telemetry. Retained events stay ordered; expired events are discarded and a timed-out event is not replayed. Writes still don't acknowledge TAK application receipt.
- Safe error categories include `E_CONFIG_INCOMPLETE`, `E_CREDENTIAL_FILE`, `E_CREDENTIAL_PERMS`, `E_CLIENT_CREDENTIAL`, `E_TLS_TRUST`, `E_TLS_TRUST_EXPIRED`, `E_TLS_HOSTNAME`, `E_CLIENT_AUTH`, `E_TAK_CONNECTION` and `E_TAK_TIMEOUT`. Check private settings/clock/network locally; never disable TLS validation or paste credentials into a report.

## Troubleshooting quick reference

| Symptom | Likely cause | Next action |
| --- | --- | --- |
| No `/dev/serial/by-id` entry | Cable, port, or board in a bad state | Different cable; BOOT+plug; check `dmesg` |
| Garbled or partial lines | Boot ROM text, or output dropped under backpressure | Ignore lines that aren't JSON; check `usb_output_drops` |
| `stopped_error` at boot | Country, band or channel rejected by the driver | Read the `error.operation` and `code`; try the other prebuilt image |
| `rf_frames` = 0 | Antenna, channel or band; or no traffic | Reference adapter on the same channel; swap antenna |
| `malformed_frames` ≈ `rf_frames` | Metadata or parser mismatch on this hardware. Seen 2026-10-30: wrong frame length, fixed in `6ee9e41`; on channel 149, non-RID subtypes with fragment bits, fixed in `aa52133` | Check `rx_reject` (callback) against the parser counters; capture a raw frame; stop and report |
| Candidates but no decodes | Unsupported variant or malformed pack | Check the version/failure counters; keep the reference capture |
| Decodes but no observations | No textual ID, or no valid location in the same pack (pre-GPS 0,0) | Wait for GPS fix; check the ID type |
| TAK queue drops/timeouts | Server, route or reader is stalled; latest pending updates replace old ones | Intake should continue; inspect safe error code, network and server readiness |
| DRONK `rejected` rising | Record fails host validation | Compare the raw JSON with the hub rules |
| Board resets when the TTY opens | USB Serial/JTAG behavior | Expected possibility; record it; there's no zero-reset guarantee |

## Record for each step

Date/time; firmware SHA-256 and channel; board revision and antenna; host kernel and IDF version; start and end counter values; reference-capture filename (stored privately); pass/fail; notes. Only claim capabilities these records demonstrate.

## Software-only verification completed before this session

Phase 1.1 code `1786cc1`: 181 automated tests passed (78 Android/core, 28 bridge, 35 DRONK Python, 10 legacy adapter, 30 firmware host tests). The suites use isolated config/home fixtures and loopback/PTY resources; they never load the operator's real configuration or contact production TAK. Desktop/mobile browser controls passed with a temporary DEMO config containing an ignored dummy receiver. Fresh channel 6 and 149 builds passed with ESP-IDF v5.5.4. Tool help and source were checked for the exact chip/flash/build/CAPTURE command syntax above; no chip read, port open, flash or RF test was performed. These software results do not satisfy any physical gate in this guide.
