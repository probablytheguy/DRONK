# DRONK technical journal

Dated engineering record. Each entry separates what was **verified on hardware or in a TAK client** from what is **implemented and tested in software only**. Aircraft serials, coordinates, network addresses and credentials are kept in private notes, never here.

## 2026-11-02: first end-to-end field test

**Summary:** a real DJI Avata's Remote ID went from the air, through the ESP32-C5 receiver and DRONK, to a TAK server and onto an iTAK map, with separate aircraft and operator markers.

### Verified

| Milestone | Evidence |
| --- | --- |
| First real Remote ID reception (indoors, no GPS) | 66 decoded Wi-Fi Beacon packs, channel 149 / 5745 MHz (receiver-reported), ODID v2, all `no_valid_position`. Imported into SQLite: 66 receptions, 1 aircraft, 1 sighting. |
| Durable capture and history | Phase 0 JSONL capture, Phase 1 SQLite store and Phase 2.1–2.3 analysis (coverage, losses, sighting quality, history API). Real database migrated v1 → v2 → v3 with verified backups. A rebuild from captures matched the migrated database exactly. |
| First real aircraft GPS positions (outdoors, on the ground, motors running) | One session: 163 receptions, all with valid broadcast positions. About 1 m spread, consistent with a stationary aircraft. Altitude 128–131.5 m HAE (WGS84 geodetic). No 0,0 positions. RSSI −76 to −87 dBm. Receiver coverage measured throughout, 0 losses, largest packet gap 13.3 s. |
| CoT correctness | The captured session was replayed offline through the LIVE path's hub and CoT generator: 162 events. UID, latitude, longitude, HAE, time, start and stale (+60 s) all matched the stored observations exactly. Type `a-u-A`. With pilot forwarding off, there was no operator position. |
| Authenticated TAK connection | Mutual TLS 1.3 to the TAK server on the local network: certificate verified against the server's hostname, client certificate accepted, connection held. The server's public name points to the site's own public address, and connections from inside the network to it time out, so DRONK connects to the server's local address (`--tak-connect-address`) while still verifying the hostname. |
| Delivery through TAK | LIVE forwarding with pilot-location forwarding on. A second, listen-only TLS client received 32 aircraft and 32 operator events from the server in 20 s, so the server accepted and redistributed them. 223+ writes, 0 failures. |
| iTAK display | The operator reported seeing both the aircraft and operator markers in iTAK. With the aircraft powered off, iTAK kept showing both as if current: it does not visibly retire markers when their CoT `stale` time passes. |

### Implemented and tested in software, not yet verified in iTAK

- **Separate operator and take-off markers.** Each is its own CoT event with its own UID (`<aircraft-uid>-operator`, `<aircraft-uid>-takeoff`), linked to the aircraft. One is sent only when the broadcast carries a valid position, and nothing is copied from the aircraft position. The Avata reports its operator location as live GNSS (type 1), so there is no take-off marker for it. *Operator marker delivery is verified (see above); this item covers the take-off variant and the code path in general.*
- **LAST KNOWN.**
  - **Trigger:** 60 s after a marker's last refresh, the bridge sends one LAST KNOWN update with the same UID. It runs on a periodic check driven by the host heartbeat, so it fires even when no packets arrive.
  - **Content:** the marker's own last valid broadcast position, labelled `LAST KNOWN`, with the real last-seen time in metadata and remarks, no kinematics, and `stale` set to the end of a 10-minute retention (`--last-known-seconds`). It is never re-sent as if current.
  - **Operator:** ages on its own broadcasts.
  - **Reasons recorded:** `no_receptions` when the receiver stayed available; `receiver_interrupted` after a USB disconnect, transport error or 15 s of receiver silence; `monitoring_stopped` on Stop. On Stop, the final reports are flushed before the TAK stream closes.
  - **Resuming:** restores live tracking with the same UIDs.
  - **Storage:** transitions are kept in SQLite (`marker_states`, migration 0003) and in the capture, and shown on the dashboard as *Last known (reason)*.
- **Symbology and callsigns:**
  - **LIVE aircraft:** standard MIL-STD-2525 types picked from the broadcast UA type: rotary-wing drone `a-u-A-M-H-Q` (the Avata), fixed-wing drone `a-u-A-M-F-Q`, otherwise `a-u-A`.
  - **LIVE operator:** `a-u-G`, a ground frame so it looks different from the air frame.
  - **Take-off:** a yellow map spot.
  - **LAST KNOWN:** the same UID becomes a grey map spot (`b-m-p-s-m`, light grey for the aircraft, dark grey for the operator). A `same-type` fallback (`--last-known-style`) keeps the live type and only relabels it.
  - **Callsigns:** the serial's last four characters. The full serial stays in the `ouitak` metadata.
  - **Basis:** these types follow existing open-source Remote ID-to-TAK gateways (DroneCOT, DragonSync). They are not verified against iTAK's rendering.

### Issues found today

- **Receiver stall (open):**
  - **What happened:** about five minutes into the first outdoor session, the C5 stopped producing output. A status line buffered on the device arrived three minutes late, and then it dropped off USB without coming back. The host was fine and the laptop didn't suspend.
  - **Recovery:** replugging the board fixed it and started a new receiver session.
  - **Cause:** unknown. Candidates are power, cable and RF proximity.
- **Buffered drops at connect:** the receiver buffers USB output drops while no host reads the port, and on reconnect the session's diagnostics counter starts with them. The derived loss windows exclude them correctly, but the dashboard's per-session delta can include them.
- **TAK card "CONNECTING":** the card showed `CONNECTING` while each write was in flight. Fixed: a send on an open stream no longer changes the state.
- **Ground truth:** broadcast positions have not yet been compared with an independent GPS fix.

### Test results

| Suite | Result |
| --- | --- |
| DRONK Python (`tools/dronk/tests`) | 145 pass |
| Linux bridge (Kotlin) | 39 pass |
| Android app unit tests (shared core) | 78 pass |
| Firmware host tests | not rerun (no firmware changes) |

### Next

1. In iTAK: confirm the new aircraft and operator symbols, the LAST KNOWN change (power the aircraft off for more than 60 s), and the return to live, with no duplicate markers.
2. Compare broadcast positions with an independent GPS fix, then fly.
3. Find the cause of the receiver stall.
4. Phase 2.4–2.6 (history UI), on hold.
