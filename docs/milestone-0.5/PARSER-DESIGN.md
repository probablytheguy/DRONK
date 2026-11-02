# Proposed tolerant Sky Spy serial decoder — design only

No implementation, USB connection, or production integration. This separates wire parsing from semantic acceptance; successful JSON decoding does not prove Remote ID validity or authenticity.

## Pipeline

`byte chunks -> bounded line framer -> strict JSON classifier -> schema checks -> semantic/provenance gate -> existing RemoteIdNormalizer`

Transport control and parsing must be separate. A parser can neither open/reopen a port nor write commands/reset lines in response to malformed text, boot output or silence.

### 1. Framing

- Accept arbitrary USB chunk boundaries, including a multibyte UTF-8 sequence or CRLF split across reads. USB packets and read chunks are not records.
- Accumulate until LF; remove one immediately preceding CR. Ignore empty/whitespace-only lines. On disconnect discard incomplete tail and reset session state; never complete it with another session's bytes.
- Proposed configurable maximum: 4096 bytes per line and bounded diagnostic storage. The S3 source uses a 256-byte JSON buffer, but the larger limit accommodates diagnostics/extensions. On overflow discard through the next LF, record an overflow count, resume framing; never parse truncated fragments.
- Decode each complete line as strict UTF-8. Reject invalid encodings instead of silently deleting bytes from IDs or JSON. No cross-line repair of interleaved output; no synthetic closing braces.

### 2. Classification

- Boot/reset/version messages become device-health events, never observations. A fresh boot clears any association state and raises a reset event; it does not trigger recovery commands.
- Recognize the exact captured legacy `{"   [+] Device is active and scanning..."}` as ignored legacy status before attempting JSON. Do not repair it into a detection.
- Parse complete object lines with a strict JSON parser. Reject duplicate keys, excessive nesting, NaN/Infinity, trailing text, arrays/scalars, and invalid escapes.
- Ignore valid status/heartbeat/ack objects without detection evidence. Allow unrelated extension keys within resource bounds. Do not discard a detection solely because an extension contains a status key; require the selected schema to determine record kind.
- Ordinary diagnostic lines and malformed JSON are ignored/countable, with rate-limited bounded diagnostics. Never salvage arbitrary substrings between braces out of boot logs. Prefixed JSON may be added later only for an evidenced explicit firmware profile, never by global substring guessing.

### 3. Candidate schema

Legacy S3 source fields are `mac`, `rssi`, `drone_lat`, `drone_long`, `drone_altitude`, `pilot_lat`, `pilot_long`, `basic_id`. Six-decimal formatting is not a requirement for numeric JSON acceptance.

- Validate MAC as six hex octets; normalize it only for sensor transport bookkeeping, never as aircraft identity. Reject all-zero/broadcast MAC as an association key. This validation is a proposed policy, not a captured property of detections.
- `basic_id`: printable ASCII string, 1–20 bytes, no padding, trimming or case folding. Empty/missing means unavailable. Do not infer SERIAL_NUMBER versus CAA_REGISTRATION_ID from its content.
- Reject booleans/numeric strings where actual JSON numbers are required. Require finite numeric fields. RSSI must be an integer; proposed policy range -127..0 dBm, with out-of-range values marked invalid rather than clamped. Final RSSI policy awaits actual capture/device behavior.
- Latitude and longitude must appear as a pair, be finite and within [-90,90]/[-180,180]. (0,0) means unavailable for this ODID profile; zero in only one coordinate is not automatically invalid. Out-of-range values are invalid, never replaced with pilot/receiver coordinates. Numeric plausibility alone does not establish freshness/validity.
- Altitude source is integer-cast ODID AltitudeGeo (WGS84 HAE meters). -1000 is unavailable; proposed supported domain >-1000 through 31767.5, consistent with the existing normalizer. Invalid optional altitude becomes unavailable with a reason. No MSL conversion and no AGL inference.
- Preserve pilot coordinates only as an unresolved operator-location candidate. The emitted schema omits OperatorLocationType; do not label them takeoff, control-station, or receiver location by guessing.
- Source does not emit speed/heading, ID type, original observation time, location timestamp, validity flags or RF transport. Do not fabricate these fields from other values or serial receive time. Record receive UTC and monotonic time separately; original observation time remains unknown.
- Recognize C5 band/channel extensions only under a separate evidenced profile. Do not infer BLE/Wi-Fi from MAC or the mode's human-readable banner.

### 4. Semantic promotion

Use three outcomes: IgnoredStatus/Diagnostic, CandidateHeld(reason), ValidatedObservation. Malformed JSON/schema failures are rejected with a bounded reason counter.

The current app's `RemoteIdObservation`/`RemoteIdNormalizer` requires a supported typed Basic ID and rejects missing/unsupported type; adapters must prove association of Basic ID and location. Existing source comments explicitly prohibit guessed IDs and MAC fallback.

For legacy S3 JSON, hold even syntactically perfect candidates when ID type, field validity/freshness or association cannot be established. Do not default ID type to SERIAL_NUMBER. Do not fill missing identity or telemetry from “last MAC,” a previous object, or another device. The older BLE code can retain stale/zero fields and the emitter lacks per-field timestamps; even one combined object is not proof of co-observation.

A future richer, verified profile can promote observations when it supplies supported ID type, exact ID, valid BasicID/location association and time/provenance contract. Validate optional role-specific locations independently, then call the existing normalizer. If only receive time is available, any receive-time-as-observation-time policy requires an explicit documented data-model decision; do not silently apply it. For the existing legacy profile there is no honest way to recover omitted metadata through parser tolerance alone.

“Validated” means schema and association requirements passed; it does not authenticate the transmitter or its claimed coordinates. The legacy raw candidate can be retained separately for diagnosis without creating an aircraft track, CoT output or location history.

## Offline acceptance cases for eventual implementation

| Input | Expected result |
|---|---|
| Existing serial.raw, every byte split point / one byte at a time | Same classification as whole-file input; zero observations; legacy status and boot/reset diagnostics recognized |
| Legacy malformed scanning line | Ignored legacy status; never parser crash or track refresh |
| Valid status=scanning / heartbeat / ack with no telemetry | Ignored status |
| Valid legacy eight-field object, plausible coordinates and ID | CandidateHeld: missing typed ID/field provenance; no CoT |
| Richer verified-profile typed/associated observation | Eligible for existing normalizer; only accepted if all semantic requirements pass |
| Blank ID, wrong JSON types, duplicate keys, invalid UTF-8, NaN, trailing junk | Rejected/held with precise reason; no data repair |
| (0,0), missing pair, out-of-range coordinates, altitude -1000 | Unavailable/invalid location semantics; never substitute pilot/receiver position |
| ID-only followed by another MAC's location-only object | No implicit join or borrowed identity |
| Oversized line followed by a valid line | Bounded memory, discard overflow through LF, parse next line |
| Interleaved diagnostic inside an object / truncated object at disconnect | Reject damaged line/tail; recover at next clean line/session |
| Same coordinates on different legitimate IDs | Remain separate identities |
| Boot text after candidate | Reset session associations; report health event, no automatic serial action |

These are design cases, not executed parser tests. Only source/history and saved capture analysis were performed in this follow-up.
