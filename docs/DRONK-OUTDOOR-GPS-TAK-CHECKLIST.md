# DRONK outdoor GPS and TAK validation checklist

Status: **run 2026-11-02, results in [the technical journal](DRONK-JOURNAL.md).**
- **Part A:** broadcast GPS positions received and the CoT replay validated. The comparison with independent ground truth, and the flight, are still to do.
- **Part B:** LIVE forwarding and iTAK display of the aircraft and operator markers confirmed.

Original status: **prepared for review, not run.** Nothing in this checklist enables LIVE forwarding automatically. Part A runs in CAPTURE (forwarding disabled). Part B (TAK) is a separate, explicitly approved session that must be started by hand with `--mode LIVE`; it is not reached by finishing Part A.

What has been verified so far (2026-11-02): Remote ID reception and Basic ID decoding indoors without GPS (66 receptions, channel 149 as reported by the receiver). **Not verified:** aircraft coordinates, tracking, track history, TAK delivery. The 5 GHz channel is receiver-reported, not independently confirmed; a reference capture is still outstanding.

## Go / no-go before leaving

- [ ] Legal and site: a location and time where flying is legal; airspace checked; permission for the site; a visual observer if required; propellers and people kept apart during any ground test.
- [ ] Aircraft: original DJI Avata, Goggles V2, Motion Controller 2, phone with GNSS connected and DJI Fly allowed to read location (the Motion Controller has no GNSS). Remote ID shows ready in DJI Fly or the goggles.
- [ ] Receiver: the unchanged Phase 2 hopping build (`66eab97b…`). **No firmware flashing** before or during the test. Note the boot `session_id` from the first status line in the capture.
- [ ] Host: laptop charged (the C5 is USB-powered from it); clock synced (`timedatectl`: "System clock synchronized: yes") because every comparison uses host UTC; at least 1 GiB free in `~/.local/state/dronk` (captures stop at 512 MiB free).
- [ ] Software: the reviewed working tree; `python3 -m unittest discover -s tools/dronk/tests -t tools/dronk/tests` passes; `python3 tools/dronk/dronk_db.py check` reports `"ok": true`.
- [ ] Reference: the independent 5.8 GHz monitor-mode adapter, set up to record which channel carries the `RID-<serial>` beacon. Without it the channel stays "receiver-reported".
- [ ] Ground truth: a way to record the aircraft's true position independently of Remote ID. For example, a phone GNSS logger next to the aircraft for a stationary ground test, and the DJI Fly flight record for the flight.
- [ ] Privacy: captures, the DB and photos stay in `~/.local/state`. Nothing from the field goes into Git. Operator and take-off positions are in the broadcast; they are recorded in the capture but kept out of tracks and provenance views.

## Part A: GPS and tracking (CAPTURE only)

1. **Start in CAPTURE.**

   ```bash
   python3 tools/dronk/command_center.py --mode CAPTURE \
     --device front=/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_<...>-if00
   ```

   Open http://127.0.0.1:8765 and click **Start DRONK**. Before the aircraft is powered, confirm these:
   - the banner shows CAPTURE;
   - the TAK card shows `DISABLED`, 0 attempts, 0 writes;
   - the capture line shows RECORDING with a new file name;
   - the USB-drop and queue-drop deltas are 0.
2. **Ground, no fix yet.**
   - Power the aircraft and run the motors safely (DJI broadcasts only with propellers turning).
   - Expect `DETECTED_NO_POSITION` with the aircraft's serial.
   - Note the time.
3. **Ground, with fix.**
   - Wait for a GNSS fix: DJI Fly shows satellites and the home point recorded.
   - Expect the same detection to move to `TRACKING` in place (one record, not two).
   - Record the phone GNSS position beside the aircraft.
   - **Pass:**
     - Broadcast latitude and longitude are within the expected GPS error of the ground truth (record the distance; about 10 m or better is typical).
     - Altitude is plausible.
     - Coordinates are never 0,0.
4. **Short flight.**
   - Fly a simple, legal pattern: out about 50 m, back, then a hover.
   - Record start and end times and the DJI Fly flight record.
5. **Out of range and back.**
   - Move the receiver or the aircraft until receptions stop for more than 60 s.
   - Expect the detection to go `STALE`, then return to `TRACKING` without a second identity.
6. **Stop.**
   - Land and disarm.
   - Click **Stop DRONK**: the capture shows CLOSED, and the console's sync imports it.
7. **Check the record (still offline).** The aircraft's Remote ID serial is ID below.

   ```bash
   python3 tools/dronk/dronk_db.py check
   curl -s "http://127.0.0.1:8765/api/aircraft/summary?type=SERIAL_NUMBER&id=<ID>"
   curl -s "http://127.0.0.1:8765/api/aircraft/sightings?type=SERIAL_NUMBER&id=<ID>"
   curl -s "http://127.0.0.1:8765/api/aircraft/track?type=SERIAL_NUMBER&id=<ID>"
   ```

   Pass:
   - The flight's sighting has `gps` of `some` or `all`.
   - Track points line up with the DJI Fly record within GPS error.
   - The track has no operator or take-off points.
   - `coverage.state` is `measured`.
   - USB drops and queue drops show no certain losses during the flight. If any occur, note their windows from `/api/coverage`.
   - `open_start` and `open_end` are false, unless capture started or ended within 30 s of the aircraft.
   - Provenance for a track point reports `verified` with a raw receiver line.
8. **Reference channel.** Compare the reference adapter's channel with the receiver-reported channel. Only a match lets the channel be called "independently confirmed".

Write down: times, distances to ground truth, channel (reported and reference), RSSI range, packet-gap statistics, losses and anything unexpected. Real coordinates and serials stay in the private notes; the repository gets only sanitized summaries.

## Part B: TAK delivery (separate session, explicit approval required)

**Do not start Part B without approval for that session.** It sends aircraft positions to a TAK endpoint.

Before the session:
- [ ] Approval recorded: which TAK endpoint, which device, which day.
- [ ] Use a test TAK Server or a phone running ATAK that you control, on a private network or VPN. Never a shared operational server.
- [ ] Credentials: client `.p12`, password file and CA file, each mode 0600 and outside the repository. No password in a shell argument.
- [ ] **Pilot-location policy decided.** The private config currently has `forward_pilot_location = true`, opted in earlier to preserve old behavior. For this validation, start with `--no-forward-pilot-location` unless operator-location forwarding is itself approved for the test.
- [ ] The receiving ATAK shows its own position, and you know how to clear test markers.

Run:
1. Start in LIVE **by hand**, outdoors, with the aircraft about to fly:

   ```bash
   python3 tools/dronk/command_center.py --mode LIVE --no-forward-pilot-location \
     --device front=/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_<...>-if00 \
     --tak-host <TEST_TAK_HOST> --tak-port 8089 \
     --client-p12 /protected/client.p12 --password-file /protected/password.txt --ca-file /protected/ca.pem
   ```

   Click **Start DRONK**. The TAK card should show the TLS stream connected, with attempts and writes at 0 until an aircraft is tracked.
2. Ground, no fix: the aircraft is `DETECTED_NO_POSITION`, and the TAK writes counter **does not move**. Positionless aircraft are never forwarded.
3. With a fix and in flight:
   - Writes rise.
   - ATAK shows one unknown-air marker for the aircraft.
   - It moves with the aircraft and its position matches the dashboard track.
   - **A TLS write is not delivery:** confirm on the ATAK screen that the marker appears and updates, and note the latency between the dashboard and ATAK.
4. No operator marker appears, because pilot forwarding is off.
5. Out of range: the marker goes stale in ATAK per the CoT stale time, and no further writes occur.
6. Stop DRONK: forwarding ends and the TAK card shows the transport closed. Restart the console in CAPTURE afterwards; LIVE is a launch-time choice and never persists.

Pass for Part B: markers seen in ATAK, matching the dashboard track, with no positionless or operator markers and no writes after STALE. Record the evidence (screenshots without real locations, counters, times) privately. Only after this may the documentation say "TAK forwarding field-verified", and then only for the endpoint and conditions tested.

## Abort conditions (either part)

- Any unsafe aircraft condition, or loss of visual line of sight: land first, record second.
- `dronk_db.py check` not ok, or the capture shows SUSPENDED: stop and keep everything as it is.
- In Part B: any marker at a wrong or 0,0 position, an operator marker with pilot forwarding off, or writes for a positionless aircraft. Stop DRONK immediately and keep the capture.
