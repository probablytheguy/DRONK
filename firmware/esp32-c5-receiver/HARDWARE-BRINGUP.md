# C5-0.1 hardware validation gates

**Pending: every item below.** No board access, flashing, RF transmission, or live reception was performed during implementation. Refer to [the capability investigation](../../docs/ESP32-C5-INVESTIGATION.md). This checklist does not authorize executing hardware operations now. The step-by-step first-session procedure with pass/fail criteria is in [docs/C5-HARDWARE-BRINGUP.md](../../docs/C5-HARDWARE-BRINGUP.md).

1. Confirm the received board is XIAO ESP32-C5 and record PCB/module revision, flash/PSRAM variant, and supplied dual-band antenna. Confirm antenna routing without changing hardware. Do not transplant S3 pin assumptions or choose its USB path.
2. Review the binary/configuration: target C5, USB Serial/JTAG, 8-MB flash compatibility, country, one allowed channel, no BLE or hopping. Record the binary SHA-256. Review any flashing procedure separately before use; do not erase or touch the existing OUI-SPY.
3. After separately authorized bring-up, check USB enumeration, receiver ID stability, new session ID after boot, JSON framing and sequence. Opening a TTY can reset a board; no zero-reset guarantee exists. Keep early ROM diagnostics separate from versioned application JSON. Do not send commands to the firmware.
4. Confirm default channel 6 tunes/readbacks correctly, `monitoring` status appears, and RF management counters increase under existing ambient traffic. A status heartbeat alone fails the RF reception gate.
5. Compare captured frame lengths against the pinned C5 `dump_len`/`sig_len` metadata and verify FCS exclusion. Observed on channel 6: `dump_len` is always `sig_len + 4` (the IDF header's "excluding the FCS" comment for `dump_len` is wrong). A raw beacon capture showed its last element ending exactly at `sig_len - 4`; the 4 bytes before `sig_len` are an FCS slot that is not a valid CRC-32. The firmware uses `sig_len - 4` as the frame length. Confirm eight-bit channel metadata and RSSI. Verify frame-size rejection, not partial decode, for >1024-byte frames.
6. Repeat fixed-channel configuration on an allowed 5-GHz channel such as 149. Confirm actual management traffic arrives. Successful build/tune does not prove 5-GHz promiscuous reception. Do not enable DFS or hopping to work around missing reception in this milestone.
7. Use an independent, already working dual-band receiver/PCAP to establish the original Avata's actual band/channel and Beacon/NAN transport during a normal planned flight. Do not generate spoofed/test RF traffic. Compare decoded Basic ID, aircraft and System/operator coordinates and timestamps against those same received bytes. Current fixtures are synthetic, not Avata captures.
8. Exercise missing/invalid Location and System fields using offline captures. Show that operator/takeoff positions never replace aircraft positions. No inference from an aircraft being powered on, flying, or visible in goggles establishes a compatible broadcast.
9. Measure queue saturation/drops, minimum heap and task stack, idle watchdog health, USB stalls/host disconnect/reconnect, and output backpressure. Ensure callback timing stays bounded when no host consumes serial output. Loss counters do not acknowledge USB delivery.
10. Independently inspect RF behavior for unintended transmissions. Firmware uses NULL/promiscuous mode with no scan/connect/AP/NAN/TX/BLE API calls, but code inspection is not an RF-emissions measurement.
11. Exercise rejected country/channel/driver failures: explicit error, `stopped_error`, no retry/reset/erase. Verify no background auto-reader touches the old S3.
12. Preserve separate local logs and receiver attribution with two receivers. The version-1 schema still needs a separately tested shared-core/Linux adapter before any TAK forwarding. No automatic routing or synthetic fallback.

Record dates, exact firmware/config hashes, host/kernel/IDF versions, fixed channel, counter deltas, independent PCAP references, errors and exclusions. Only claim capabilities demonstrated by those records. BLE, hopping, cross-message identity association, authenticated identity and synchronized receiver UTC are outside C5-0.1.

## SUSPECTRUM standalone gates (pending, no hardware exercised)

- With safe independent power, boot with no data host; verify passive fixed-channel reception begins without commands. Compare `session_id`, uptime and reset reason when attaching USB later.
- Keep a known supported RID source available, stop/start the host, disconnect/reconnect USB data, and verify increasing RF/decode counters and continuous uptime. A host status alone is not proof of RF reception.
- Verify that opening/closing the actual OS TTY does not unexpectedly reset this board; no zero-reset guarantee is made. Distinguish USB reset from loss of its only power.
- Saturate output or stop reading while RF traffic continues; verify bounded heap, increasing output drops, no stalled decode loop, and clean LF framing after recovery.
- In a dedicated test build, deliberately stall the subscribed decode task; verify watchdog reboot, new session, reset reason and automatic passive monitoring recovery. Do not inject faults into the production receiver without a separate authorized procedure.
- Confirm actual 2.4/5-GHz reception, antenna/power integrity, correct selected channel, and long-duration thermal/heap behavior. No transmit/active scan testing is required or authorized.

## Preflight hardening gates (pending hardware authorization)

- Confirm `C5-0.2-preflight`, selected channel, uptime and reset reason in hello/status after independently powered autonomous boot, including attaching a host after hello was missed.
- Use passive observations of a known supported RID source on channels 6/149 separately; record RF/candidate/decoded counts and malformed/version/decode failures. A status heartbeat or RF callback alone is not a confirmed detection. Preserve aircraft/operator unknowns.
- Observe sustained load with and without a USB reader; compare packet queue drops, USB output drops, minimum heap and status cadence. Check idle/watchdog/USB progress and retained framing after host reconnect. Host-model throughput is not an RF acceptance threshold.
- Confirm real task-watchdog recovery creates a new session and reports a reset reason; verify the host discards partial old lines and accepts the new session after USB reconnect. The host deliberately rejects a new session without hello while an old session remains bound and no transport reconnect was observed; if this board can reboot without a USB disappearance and lose hello, stop/restart host monitoring and record this as a validation failure for follow-up, rather than weakening replay protection silently.
- Verify passive behavior using an independent RF observation method where available; source/build inspection proves no application active-scan/association/TX call, not the absence of every driver-level on-air emission. Do not inject synthetic RF fixtures.
