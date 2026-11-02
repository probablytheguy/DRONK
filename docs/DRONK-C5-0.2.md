# DRONK C5-0.2 — host integration

DRONK means **Drone Reconnaissance & Observation Network**. This milestone adds a receiver-aware host path and the local Command Center. The repository, Android package and existing OUI-SPY launchers keep their existing names and behavior. No firmware was changed or flashed. No physical USB device was opened. RF reception remains unverified.

## Pipeline

```
Explicit USB paths (1–8), read-only 115200 8N1
  -> Python selector, bounded chunks with stream index and host receipt time
  -> Linux bridge --dronk
  -> DronkReceiverHub: independent framing/session/counters per stream
      -> C5 version dispatch / validation / RemoteIdNormalizer
      -> unchanged legacy OUI-SPY parser and provisional policy
  -> shared AircraftTracker -> CotGenerator -> optional existing TLS transport
  -> JSON telemetry -> localhost Python HTTP/SSE -> SUSPECTRUM log console
```

The C5-0.1 firmware's `ouitak.receiver` discriminator is accepted unchanged. New `dronk.receiver` records use the same version-1 schema. Unknown versions reject explicitly. Protocol envelope fields cannot fall back into the legacy decoder. Once a stream binds to a C5 identity, it cannot silently switch to a different ID or legacy detections. Optional expected-ID bindings make this explicit from startup. Wire claims are unauthenticated.

Every stream owns its LF/CRLF framer, decoder/session state and host counters. Maximum eight streams; bounded frame size 4096 bytes, C5 line length additionally constrained to the firmware's 3071-byte contract. Duplicate configured paths/inodes are refused. Aliases must be stable across host restarts for legacy provisional identity stability. Stable `/dev/serial/by-id` paths are preferable to transient ttyACM numbers; no device discovery or guessed C5 USB identifiers are introduced.

## Identity, coordinates and time

Typed aircraft identity remains `(ID type, exact Basic ID)`, independent of receiver, USB path and source MAC. Two receivers seeing the same typed ID share a track. Legacy untyped IDs remain host/receiver-alias scoped and retain the provisional label. Synthetic identity namespaces remain separate from live tracks.

The host validates the full C5 message: types, protocol/version, identity, session, sequence, channel/frequency agreement, RSSI, association, coordinates, altitude, and raw timestamp ranges. A decoded frame or RF counter never becomes an aircraft marker. `hello`, `status`, `error`, diagnostics, malformed records and incomplete lines do not generate CoT.

Aircraft location must be independently valid; missing/zero/invalid aircraft coordinates reject. Operator latitude/longitude cannot substitute for aircraft position. Operator type 0 maps to takeoff, types 1/2 to the existing control-station report; raw type and validity remain preserved. Missing operator location or altitude remains unavailable. No receiver position is invented or used as aircraft position.

`Detection.dronk` preserves stream, receiver ID, session, full unsigned sequence, RF transport/channel/frequency/RSSI, transmitter MAC, message counter, UA type, device receive/serialization uptime, Location seconds-after-hour, System seconds-since-2019, and independent operator validity/position. CoT carries an additive escaped `<dronk>` extension. Old detections without this metadata generate their existing XML behavior. Android's current USB path is unchanged; it does not claim C5 device support.

Host receipt UTC is sampled by Python before each chunk is passed to the JVM. Presentation observation time is explicitly **host receipt minus device queue delay** (`uptime_us - received_uptime_us`). Raw RF timestamps are retained separately; no complete broadcast UTC is fabricated from seconds-after-hour or uptime. Delays above one day reject. Already stale buffered observations are ignored by the existing tracker. This policy cannot reconstruct time spent queued in USB/host buffers; it is not an RF freshness guarantee. The stream's final chunk determines receipt time for a line split across reads.

## Sessions, duplicate observations and stale tracks

- Sequence and uptime must increase within a session. Replay/out-of-order sequence records cannot refresh a track. Full uint64 sequence values are parsed without floating-point conversion.
- Disconnect discards that stream's partial frame but retains sequence protection. Reconnect with the same session continues only with new sequence values. A new session can bind after reconnect, or via a valid hello while connected. The last 16 retired session IDs are held against replay; memory remains bounded. Receiver identity stays bound across reconnects; replacing a receiver requires an intentional host restart/reconfiguration.
- Identical identity/location/operator/raw-time payloads within two seconds are deduplicated across receivers. The duplicate reception and its receiver/RF metadata remain visible in history, but no track refresh or extra CoT write occurs. Sequence replays are counted and discarded. The bounded payload cache holds at most 2048 entries.
- The shared tracker expires tracks after 60 seconds by default. Status, receiver errors, invalid records and duplicate observations do not extend the timeout. A disconnect does not immediately delete a recent aircraft claim: last-seen tracks remain until stale, while the receiver and console show disconnected status.
- A read error closes only that stream. If its node remains present after two seconds, it enters transport-error state without reopening. Reconnect is attempted only after node disappearance and return, with a one-second delay. Initial open/configuration failure stops that stream without retry. Other streams continue. These rules do not guarantee reset-free TTY opens.

## Launch

See [Command Center setup](../tools/dronk/README.md) for one/two receiver commands, DEMO, forwarding configuration, exports and secure access. The legacy `tools/milestone-0.8` scripts remain valid. The new `--dronk` dispatch is opt-in; the original Linux CLI does not change its default mode.

Local pipe envelope (not a USB or network protocol): big-endian `u8 kind`, `u16 stream index`, `i64 host epoch milliseconds`, `u32 payload length`, followed by at most 4096 bytes. Kinds: 1 data, 2 connected, 3 disconnected, 4 global stop, 5 global tick, 6 stream transport error. Global index is 65535. The single JVM owner serializes all stream feeds; no tracker concurrency is introduced.

## Validation and limits

- `:app:testDebugUnitTest`: 75 existing tests pass.
- `:linux-bridge:test`: 17 tests pass (12 new DRONK tests, five existing bridge tests).
- Existing Python Linux adapter: 10 tests pass.
- New HTTP/SSE and pseudo-terminal suite: 12 tests pass, including two real PTYs through the JVM, read-error isolation, disappearance/reconnect, exports, origin/Host restrictions and bounded history.
- Headless Chromium desktop and iPhone-sized layouts: live SSE, two synthetic receivers/aircraft, unavailable altitude, marker-only aircraft coordinates, export, text/XSS safety, disconnect UI, no page overflow and no tile requests by default pass. This is Chromium mobile emulation, not physical iPhone/Safari certification.

Remaining gates: real C5 USB identities and reconnect behavior, simultaneous physical receivers, actual 2.4/5-GHz reception and Avata transport/channel, driver counter accuracy, clock/queue/USB latency, sustained backpressure, authenticated TLS server delivery and actual TAK application marker display. No real server traffic was sent. All [C5 hardware gates](../firmware/esp32-c5-receiver/HARDWARE-BRINGUP.md) still apply.

The current manual supervisor, standalone receiver lifetime, protected host controls and validation gates are documented in [DRONK operating model](DRONK-OPERATING-MODEL.md). The web console defaults to host STOPPED; Start launches the USB/JVM worker and Stop closes it.
