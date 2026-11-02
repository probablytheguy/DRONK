# DRONK capture files (Phase 0)

Every started host session writes one append-only JSONL file: one JSON object per line, UTF-8, newline-terminated. Schema `dronk.capture`, version 1. This is the native log that later SQLite imports will read.

## Where and when

- Default directory: `$XDG_STATE_HOME/dronk/captures`, normally `~/.local/state/dronk/captures` (mode 0700; files 0600). Override with `--capture-dir`, `DRONK_CAPTURE_DIR` or `capture_dir` in the private TOML. The directory must be absolute and outside the repository. `--no-capture` disables it.
- CAPTURE and LIVE capture by default. DEMO captures only when a directory is given explicitly, and its files are named `dronk-demo-…` with `"synthetic": true` in the header, so synthetic sessions never sit beside field captures by default.
- File name: `dronk-capture-<UTC start, ms>-<first 8 hex of capture_id>.jsonl`, for example `dronk-capture-20261102T141327.065Z-846b59c9.jsonl`. Names sort in start order.
- The file is created with `O_CREAT | O_EXCL`. A restart, a crash recovery or a name collision can never reopen, append to or overwrite an earlier capture; a collision refuses to start instead. Nothing in DRONK deletes captures.
- The capture opens **before** the bridge starts or any TTY is opened. If it can't be created (directory unusable, less than 512 MiB free), monitoring does not start and the dashboard shows `E_CAPTURE`.

## Record envelope

Every record has `capture_id` (UUID, same for the whole file), `n` (0, 1, 2, … with no gaps in a complete file), `t` (UTC, millisecond ISO-8601 with `Z`) and `type`.

For `rx_line` and `usb` records, `t` is the time the worker read the bytes from the TTY (the same receipt time the bridge gets). For other records it is the write time.

| `type` | `layer` | Content |
| --- | --- | --- |
| `capture_start` | — | Header: `schema`, `version`, `mode`, `synthetic`, `streams` (public stream aliases, never device paths), `previous_capture` (the newest earlier file name in the directory, or null), `limits`. |
| `rx_line` | `raw_receiver` | One line exactly as the receiver sent it, **before** any validation, deduplication, tracking or synthetic filter. `line` (UTF-8 text) or `line_b64` (bytes that aren't UTF-8). `truncated: true` if the line exceeded 4,096 bytes (the rest up to the next newline is discarded, not split into a new record). `partial: true` for an unterminated line flushed at a disconnect or at shutdown. Includes boot noise and lines the hub later rejects. |
| `usb` | — | `connected`, `disconnected` or `error` for a stream, with the running disconnect count for this capture. `closed_by_host` is the host closing its own port on Stop; it is not counted as a disconnect. |
| `bridge` | `decoded_remote_id` | A Remote ID record the bridge validated: `rid_reception` (decoded, not trackable, for example no position), `observation` (tracked) or `reception` (decoded with a position but not accepted by the tracker). These are confirmed Remote ID messages. |
| `bridge` | `marker` | The bridge's outbound marker transition (`marker_state`): `LIVE` or `LAST_KNOWN` for the aircraft, operator or take-off marker, with reason, last-seen time and that role's last broadcast position. Indexed into `marker_states`. |
| `bridge` | `telemetry` | Host/receiver telemetry: `ready`, `receiver`, `tak`, `error`. Not Remote ID. Unchanged repeats of `receiver` and `tak` are skipped; `tracks` is not written. |
| `diagnostics` | — | Session-relative receiver counters (see below). |
| `receiver_session` | — | The receiver's boot session changed on a stream (reboot or a different board): `session_id`, `previous_session_id`, `reset_reason`. |
| `db_error` | — | The live SQLite index failed (`error`, `detail`, `indexed` so far). Capture continues; the console's sync imports this file later. |
| `capture_suspended` | — | Writing stopped for the rest of the session: `low_disk_space` (under 512 MiB free) or `max_file_size` (2 GiB). Monitoring continues. |
| `capture_end` | — | Session boundary: `reason` (`requested`, `duration`, `bridge_exit`, `receiver_failure`, `error`), final counts, per-stream session deltas. Written even after a suspension. |

Raw RF statistics (frame counts in `status` lines, `rf_frames` in diagnostics) are receiver telemetry. Only `layer: decoded_remote_id` records are Remote ID.

## Diagnostics

The first `receiver` telemetry after a stream connects (or after its boot session changes) sets the baseline. `delta` is the change since then for `usb_output_drops`, `malformed_frames`, `queue_drops`, `oversize_drops`, `output_errors`, `errors`, `rf_frames`, `candidate_frames`, `decoded_packs` and `rid_receptions`, as far as the firmware reports them. `lifetime` holds the receiver's own totals. A record is written for the first delta and on every change to a problem counter (the first five); traffic counters ride along. `usb_disconnects` comes from the host's own USB events. The dashboard shows the same deltas for the running session.

**Stale buffered output on connect.** While no host reads the port, SUSPECTRUM's USB buffer fills; it then holds a couple of old status lines, and each dropped record leaves one resync newline. On the next connect the host receives those first: old statuses, then one empty line per `usb_output_drops` increment, then current output. All of it is kept as raw `rx_line` records (the empty lines appear as `"line":""`). A status whose `uptime_us` jumps more than 10 s beyond the host time that passed since the previous status marks the earlier ones as stale: the baseline is set again from the fresh status, and later diagnostics carry `rebaselined: "stale_buffered_status"`. Observed on hardware 2026-11-02 (two sessions).

Limit: anything that happened between connecting and the first fresh status (up to about 5 s) is in the lifetime total, not the delta.

## Provenance and deduplication keys

- One receiver emission: (`session_id`, `seq`). `session_id` is the receiver's per-boot UUID, `seq` its monotonically increasing record number (a decimal string, u64). Both are in the raw line and, since Phase 0, in every decoded record: `rid_reception.reception.session_id/seq` and `observation.aircraft.rf.session_id/seq`. The same key in two captures (overlapping imports, a re-import of the same file) is one observation.
- `receiver_id` is the public stream label in decoded records. Raw lines keep the firmware's own `receiver_id`, which can contain a factory identifier: captures are private.
- `message_counter` is the aircraft's own Remote ID counter, kept for cross-receiver matching later; it is not unique on its own.
- One record in one capture: (`capture_id`, `n`).
- Older captures and exports without `session_id`/`seq` (for example the 2026-11-02 field log) can't be deduplicated by key; the dashboard counts them as before.

The bridge already drops a repeated (`session_id`, `seq`) from a live receiver, and the dashboard ignores a repeat it has already counted. The raw layer keeps both copies, so the capture shows what the receiver actually sent.

## Durability

Each record is a single `write()` to an `O_APPEND` descriptor, so an interruption can truncate only the final line. The file is fsynced every 2 seconds, on USB disconnects and at the end. Free space is checked every MiB written and every 10 seconds.

Reading: `capture_log.read_capture(path)` keeps every complete record and reports `complete` (ends with `capture_end`), `truncated_tail` (final line without a newline, never parsed), `malformed_lines` and `consistent` (one `capture_id`, with a header). A capture without `capture_end` is an interrupted session (crash, kill, power loss); its records up to the last fsync are intact. Restarting never repairs or appends to it: the next session gets a new file whose header names it as `previous_capture`.

## Privacy

Captures contain aircraft serial numbers, transmitter MACs, raw receiver identifiers and, once a GPS fix exists, aircraft and operator positions. Keep them private; never commit them. Sanitize (as was done for `tests/fixtures/field-2026-11-02-avata-receptions.jsonl`) before using one as a fixture or sharing it.
