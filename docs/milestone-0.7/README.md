# Milestone 0.7 — Synthetic flight and icon test

## Safety and scope

This is a Linux-only extension of the Milestone 0.6 headless test harness. It does not change Android production code, firmware, certificates, or TAK Server configuration. It uses the existing protected client PKCS#12 and trusted CA paths through JSSE; private keys stay in memory and credential values are not logged. All transmitted observations are synthetic and use `how="m-s"`, `synthetic="true"`, `verified="false"`, and a prominent simulation remark.

## Icon comparison sent

On 2026-10-24, the tool established TLS 1.3 with hostname and CA validation, presented the configured client certificate, and wrote four separate CoT events to the live server stream. The operator subsequently confirmed that all four candidates appeared in TAK Aware.

| CoT type | Callsign | UID | Test coordinates |
| --- | --- | --- | --- |
| `a-u-G-U-U-D` | `SYNTH-ICON-1` | `ouitak-test-d6ae342e-4963-35a2-b13b-1fff93fd7840` | 38.62700, -90.19940 |
| `a-u-A-M-H-R` | `SYNTH-ICON-2` | `ouitak-test-43dfd80c-28dd-30fb-bfa3-1cee011df787` | 38.62718, -90.19922 |
| `a-u-A-M-H-Q` | `SYNTH-ICON-3` | `ouitak-test-f1c66555-bc91-3436-887b-7b54654860d8` | 38.62736, -90.19904 |
| `a-u-A-M-F-Q` | `SYNTH-ICON-4` | `ouitak-test-b2bbb36c-4774-3d74-acd6-d3a5b7357949` | 38.62754, -90.19886 |

Each icon candidate had a 180-second stale time. The four points were offset by roughly 20 metres to keep them distinguishable while remaining adjacent.

**Operator-confirmed TAK Aware result:** `a-u-A-M-H-Q` (`SYNTH-ICON-3`) is the preferred unmanned rotary-wing presentation symbol. Preserve unknown affiliation. Rotary-wing is a configurable presentation default only; it is not verified aircraft identification. The other three requested candidates also appeared. This preference is confirmed for TAK Aware only and remains untested in iTAK.

## Interactive flight test

Start the authenticated test process from the repository root, with shell tracing disabled and the existing protected local files configured as in [Milestone 0.6 headless instructions](../milestone-0.6/HEADLESS-TEST.md):

```bash
bash tools/milestone-0.6/run-headless.sh --flight
```

The session authenticates and waits without transmitting a flight event. Type these commands in its terminal:

| Command | Effect |
| --- | --- |
| `start` | Begin immediately, then send every 5 seconds. |
| `pause` | Suspend updates; the last point can stale after 15 seconds. |
| `resume` | Continue the same UID and waypoint sequence. |
| `stop` | End updates; sends no delete event; last point can stale. |
| `quit` | Close the session. |

The flight UID is stable across the run and the callsign is `SIM-DRONE-FLIGHT-001`. Its ten-point looping path is near downtown St. Louis (about 38.6265–38.6312 N, 90.1994–90.1914 W). Synthetic HAE, speed, and course vary by waypoint; timestamps are generated at send time and each event is stale 15 seconds after its own timestamp. The route repeats until stopped. Each event retains the same UID so a client should update one marker if it accepts the input as a single entity.

The live run used UID `ouitak-test-7ec2cabc-20bb-3c9c-a699-b94d2f70ec0e` and callsign `SIM-DRONE-FLIGHT-001`. The process was gracefully stopped with its `stop` command at 2026-10-24 22:04:00 UTC; it sent no delete event, and the last event's 15-second stale interval was allowed to elapse. The TAK Server was not stopped or reconfigured.

**Operator-confirmed TAK Aware result:** the flight appeared, updated the same marker approximately every five seconds, and showed altitude, speed, and course updates. No duplicate aircraft markers were observed. The synthetic warning appeared correctly in Remarks.

TAK Aware recorded a flight log for `SIM-DRONE-FLIGHT-001`: duration 2 minutes 50 seconds, distance 2,860 feet, mapped flight path visible, playback control available, and KML export offered. Track recording configuration and retention policy were not verified. Stale-marker dimming/removal was not verified.

These are operator-observed client results, distinct from harness diagnostics. The harness itself confirms only TLS setup and socket writes, not TAK Server application acknowledgements.

## Track-history protocol note

The transmitted events are ordinary repeated CoT `<event>` position reports using one UID and current time/start/stale values, with `<point>`, `<track speed course>`, and synthetic telemetry detail. They do not encode a polyline or archived route. The TAK Aware v1.5.2 guide describes starting Track Logs for another marker and displaying active tracks, subject to its configured threshold ([guide](https://www.flighttactics.com/files/takaware-v152.pdf)). The iTAK App Store listing describes Breadcrumb trails created from a contact or any marker ([listing](https://apps.apple.com/us/app/itak/id1561656396)). This suggests clients can record repeated native position updates using their own breadcrumb tools; it does not establish automatic, server-retained history. No extra path object is sent. Whether historical data remains after source transmission stops must be verified in each client and may require explicitly saving/publishing a client-created track or route.

## Verification status

| Behavior | TAK Aware | iTAK |
| --- | --- | --- |
| Icon candidates received/displayed | Confirmed by operator | Unverified |
| Preferred icon | `a-u-A-M-H-Q`, rotary-wing presentation default with unknown affiliation | Unverified |
| Flight marker moves under one UID | Confirmed; ~5 s updates; no duplicates observed | Unverified |
| Heading, speed, and altitude update | Confirmed by operator | Unverified |
| Synthetic warning in Remarks | Confirmed by operator | Unverified |
| Breadcrumb/flight path and playback | Confirmed; 2:50, 2,860 ft, map path and playback control | Unverified |
| KML export offered | Confirmed by operator | Unverified |
| Recording configuration and retention policy | Unverified | Unverified |
| Stale marker dims/expires | Unverified | Unverified |

## Outstanding Milestone 0.7 items

- Stale-marker removal behavior.
- iTAK icon, live-update, telemetry, breadcrumb/history, and stale behavior.
- Android application TLS delivery to the remote TAK Server.
- Physical Android USB host transport.
- Real Remote ID reception from an actual broadcasting drone.

TAK Aware's confirmed results are operator visual observations. iTAK compatibility remains unverified; no result is inferred from a TLS handshake or socket write.
