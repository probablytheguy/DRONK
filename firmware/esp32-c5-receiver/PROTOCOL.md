# DRONK / SUSPECTRUM receiver protocol version 1

UTF-8 JSON objects, one LF-terminated record per line, maximum **3071 bytes including LF** (3072-byte C buffer includes NUL). No binary prefix, checksum, host commands, or RF transmission. Boot ROM/panic output may precede or interrupt application records; consumers must discard non-protocol diagnostics and incomplete lines, never salvage embedded JSON. JSON string values are escaped. Unknown protocol versions must be rejected explicitly by future consumers; no reinterpretation as a legacy record.

Common fields on `hello`, `status`, `error`, and `observation`:

| Field | Meaning |
| --- | --- |
| `protocol`, `version` | `ouitak.receiver`, integer `1` |
| `type` | Record category |
| `receiver_id` | Stable `c5-` plus factory base MAC, or configured override (1–64 bytes). Attribution only, not authentication. Override collisions must be prevented by the operator. |
| `session_id` | 32 hexadecimal characters generated at boot using `esp_fill_random`. Not a cryptographic authentication token or guaranteed globally unique UUID. |
| `seq` | Unsigned incrementing output-attempt sequence starting at zero, scoped to session; output failure can create gaps. |
| `uptime_us` | Device monotonic microseconds at serialization; integer, not UTC. |
| `reset_reason` | Additive integer ESP-IDF `esp_reset_reason_t` from boot, repeated on every record. Older v1 firmware may omit it; host displays unavailable. 0 unknown, 1 power-on, 3 software, 4 panic, 5 interrupt watchdog, 6 task watchdog, 7 other watchdog, 9 brownout, 11 USB. See pinned IDF `esp_system.h` for the complete enum. |
| `receiver_utc` | Always `null`: no synchronized onboard UTC source. |

`hello`: firmware milestone, board identifier, pinned IDF version and decoder commit, `passive:true`, `hopping` (true for the default scanning build, false for a fixed-channel diagnostic build), `ble:false`, `requested_channel` (the fixed channel, or null when hopping), and `hardware_reception_verified:false`. This is emitted before initialization; it does not claim successful tuning. Application version in the binary is 0.3.0 (`C5-0.3-scan`). Match the binary hash/build record for exact provenance.

`status`: every five seconds while the receiver decode loop runs, independently of USB/host presence. `state` is `monitoring` after successful configuration, or `stopped_error`. `channel`/`frequency_mhz` are read-back tuning metadata (the channel tuned when the record was built, which changes between records while hopping), or null before a successful readback. Status is not a radio self-test. `free_heap_bytes` and `minimum_heap_bytes` report heap pressure. Cumulative per-session counters:

| Counter | Definition |
| --- | --- |
| `rf_frames` | Management-frame callbacks delivered by the configured driver filter, including subsequently rejected/oversized/queue-dropped frames. Not all ambient RF energy or every transmitted frame. |
| `candidate_frames` | Dequeued frames containing a recognized RID Beacon IE or supported NAN service descriptor, counted even if decode fails. Dropped/unparsed frames cannot be classified. |
| `decoded_packs` | Structurally valid packs with at least one successfully decoded supported member and no known-message decode failure, including those held for missing/unsupported identity or position. Safely skipped slots are allowed. |
| `decoded_messages` | Sum of successfully decoded supported member messages in successfully decoded packs, not unique aircraft or deduplicated transmissions. |
| `detections` | Semantically eligible observation records successfully formatted; USB delivery is not acknowledged. |
| `errors` | Driver metadata, malformed frame/pack, decode, or initialization failures; held semantically incomplete data is not an error. |
| `queue_drops` | Zero-wait callback enqueue failed; frame was lost. |
| `oversize_drops` | Management payload exceeded 1024 bytes. Never decode a truncated copy. |
| `malformed_frames` | Invalid driver metadata or malformed frame/pack lengths, nesting or nonzero trailing data; includes non-RID malformed management frames. |
| `unsupported_versions` | Unsupported ODID version occurrences: one outer header, or each safely skipped future-version member. Not a count of unique versions or mutually exclusive frame outcomes. |
| `decode_failures` | Known member decoder failure or invalid/duplicate known-member content after structural checks. |
| `usb_output_drops` | Record output failure/drop attempts at output queue/unavailable output or short/timed-out USB writes, including records discarded while resynchronizing. Not bytes, exact end-to-end loss or confirmed host delivery; formatter failures belong only to `output_errors`. |
| `skipped_messages` | Complete reserved-type or future-version member slots skipped without interpreting their payload. |
| `output_errors` | Formatter failure, bounded output queue overflow, or bounded USB driver short write/timeout; not an exact count of lines lost in host/USB buffering. |

`status` also carries `scan` while monitoring: `mode` (`hop` or `fixed`), `dwell_ms`, `hot_ttl_s`, `current` (equal to `channel`) and `on_channel_ms` (both null before the first tune), and saturating 32-bit `switches`, `tune_errors` (failed or mismatched channel read-back), `transition_drops` (frames tagged with the channel being left, or received during a switch, dropped without counting as malformed), `switch_us_last` and `switch_us_max` (set-channel plus read-back time). `channels` has one row per scan channel, `[channel, dwell_ms, rf_frames, candidates, decoded, seconds_since_last_decode|null, hot]`, all saturating at 2^32-1. Hopping repeats `[6, 149, sweep, 6, 149, hot-or-sweep]` over the US non-DFS allowlist; a channel with a Remote ID candidate is `hot` for `hot_ttl_s` and revisited in the hot slot without stopping the sweep. Scan rows are coverage telemetry, not detections. The worst-case status line is 2,753 bytes, within the host's 3,070-byte limit.

`error`: initialization `operation` and numeric ESP-IDF `code`. Firmware stops monitoring without retry. Packet errors increment counters instead of flooding serial with per-frame error lines. Stop/disable errors during cleanup are best-effort; status remains stopped.

`observation`: emitted only for a successfully decoded and validated same-pack identity and aircraft position. Repeated packets can produce repeated records; there is no deduplication, authentication, or cross-message association.

| Field | Meaning |
| --- | --- |
| `odid_protocol_version` | Additive diagnostic outer ODID pack version (0, 1 or 2); distinct from the USB JSON protocol version, which remains 1. |
| `received_uptime_us` | Device monotonic time sampled in RX callback, distinct from serialization. |
| `transport` | `wifi_beacon` or `wifi_nan`; no BLE claim in this milestone. |
| `channel`, `frequency_mhz`, `rssi_dbm` | Actual packet RX metadata; MHz is derived from the supported channel map. No frequency guessed from aircraft model. |
| `transmitter` | Source MAC from this management frame; transport metadata, never aircraft identity. |
| `message_counter` | Unauthenticated one-byte ODID service-info counter. |
| `association` | `same_pack`: fields decoded from one pack. Does not certify source authenticity, freshness or an uncompromised transmitter. |
| `basic_id` | `id_type` (1 serial, 2 CAA), numeric `ua_type`, exact printable textual `value`. Unsupported/binary/ambiguous identities produce no observation. |
| `aircraft` | Location object; always valid for an emitted observation. |
| `location_timestamp_seconds_after_hour` | Raw decoded Location timestamp, seconds after full UTC hour, or null for unavailable/out-of-range. Not a complete epoch timestamp. |
| `operator` | `location_type`, `location`, `timestamp_seconds_since_2019`, each unavailable when its System data is absent/unsupported. |
| `authenticated` | Always false. No signature verification or trusted identity claim. |

Locations carry `validity` (`valid`, `unavailable`, `invalid`), `latitude`/`longitude` in WGS84 degrees, and `altitude_hae_m` in meters above WGS84 ellipsoid. `(0,0)`, absent data, or out-of-range coordinates never produce a valid position. Unavailable/invalid coordinate pairs serialize as nulls; unavailable altitude (-1000) serializes independently as null. `valid` denotes decoder success and numeric plausibility, not certified accuracy or freshness.

Operator location type 0 is takeoff, 1 dynamic GNSS, 2 fixed, 3 reserved. Retain this distinction; do not label type 0 as current operator/control-station location. Operator validity cannot fill/repair aircraft validity. System time is the raw nonzero seconds since 2019 field; no conversion or freshness guarantee is implied. Zero means unavailable here. No source UTC is invented, no host receipt time is embedded by firmware, and no speed/heading/operator ID is inferred.

Supported framing profiles:

- Unfragmented/unprotected version-0 IEEE 802.11 management frames, without radiotap or FCS.
- Beacon: complete 24-byte header plus 12-byte fixed body, bounded IE walk; vendor IE 221 with OUI `FA:0B:BC` or `90:3A:E6`, type `0D`, counter and a bounded ODID pack. Duplicate recognized IEs reject.
- NAN public vendor action: destination `51:6F:9A:01:00:00`, category/action/OUI/type `04 09 50 6F 9A 13`, bounded little-endian attribute walk, service descriptor attribute 3 for service `88:69:19:9D:92:09`. Only follow-up service-control `10` with service-info, no optional layout fields is supported. Other NAN layouts are rejected/ignored, not guessed. No NAN synchronization transmission.
- Packs: compatible ODID protocol versions 0, 1 and 2; 1–`ODID_PACK_MAX_MESSAGES` fixed 25-byte members. Declared count/size must fit completely. Zero trailing padding is accepted only inside the length-delimited RID payload; nonzero trailing bytes reject. Transport IE/attribute/service-info lengths remain strict.
- Complete reserved member types 6–14 and future-version member slots are skipped without decoding their bodies. A future-version outer pack is unsupported; nested pack type 15 always rejects (no recursion). At least one supported successfully decoded member is required to count a decoded pack; observations still require independently valid same-pack Basic ID and aircraft Location. Skipping a missing/future ID or Location never fabricates a detection. Known-message decode failures reject the pack.
- Duplicate Basic ID types, duplicate auth pages and duplicate singleton message types reject. Multiple supported IDs of different types hold rather than choosing an identity. For legacy System messages, the timestamp is unavailable before version 2; operator altitude is conservatively unavailable for version 0. Reserved legacy bytes cannot become invented newer fields.

Multiple receivers must retain separate byte framers and session/sequence state. The same typed aircraft ID can be observed by multiple receiver IDs; neither MAC nor receiver ID should become a shared aircraft track key. Linux integration assigns host receipt UTC separately, retain RF provenance, and require explicit forwarding mode. The DRONK Linux path dispatches this versioned schema alongside legacy OUI-SPY input. See `docs/DRONK-C5-0.2.md`.

## Standalone output and recovery

Power-on starts passive monitoring with no host handshake. The receive callback has a zero-wait bounded packet queue; the decode task has a separate four-record zero-wait output queue. A dedicated USB Serial/JTAG task uses a bounded driver buffer and 20-ms write waits. Short writes increment `output_errors`; a newline is attempted before the next record to resynchronize framing. Disconnected output is lossy, not persistent storage or acknowledged delivery. Buffered records can arrive after reconnect; sequence/uptime gaps must be tolerated and do not imply RF failure. USB connection changes never issue a Wi-Fi stop command.

The decode task subscribes to the IDF task watchdog (10 seconds, panic/reboot). A recovered boot gets a new session and reports its reset reason and uptime on all messages, allowing hosts that missed hello to identify it. Initialization/configuration failures still fail closed into `stopped_error`; there is no destructive NVS recovery, factory restore, or automatic radio reconfiguration. The watchdog covers stalled tasks, not every semantic radio failure. These are implemented/build-verified behaviors; detached USB, saturation, watchdog reset and real RF reception remain hardware gates.

Preflight draining processes up to 32 packets or 5 ms of work per slice, then yields one FreeRTOS tick only when that bound is hit. Subsequent receives in a slice have zero wait; an empty queue blocks naturally on the next first receive (up to 100 ms). The watchdog is fed and status serviced between slices. Queue drops increment only on actual zero-wait packet enqueue failure, never by subtracting callback/decode counters. See [preflight validation](../../docs/C5-PREFLIGHT-HARDENING.md).
