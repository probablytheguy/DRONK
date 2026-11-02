# DRONK security remediation — Phase 1

This phase removes personal infrastructure metadata from the tracked working tree and makes forwarding configuration explicit. It does not authorize publication of Git history. Firmware baseline `9c406fd`, passive radio behavior, the USB protocol and the read-only receiver controls are unchanged. No hardware or production TAK Server was accessed. No credential value was read for migration, changed, rotated or deleted.

## Configuration

Requires Python 3.11+ for standard-library TOML. Keep private configuration outside the checkout:

```bash
mkdir -p ~/.config/dronk
chmod 700 ~/.config/dronk
# Only if no private configuration already exists:
cp -n config/dronk.example.toml ~/.config/dronk/config.toml
chmod 600 ~/.config/dronk/config.toml
```

The remediation preserved the former runtime certificate hostname and credential file references in a new protected local TOML file, using CAPTURE as its safe default. It did not overwrite an existing file, copy key/password contents, or test the live connection. If the default already existed, the migration uses a separate `legacy-tak.migrated.toml`; select it with `DRONK_CONFIG` or merge locally. The former alternate integration-test destination is retained only as a private comment for operator review. Review network routing locally before choosing it as `connect_address`.

The checked-in example contains placeholders, not the migrated settings. Never copy actual values back into the example or documentation. Local configuration must be a private regular file, outside the repository. Credential files also belong outside Git; use protected directories and mode 0600 for client PKCS#12 and password files, and protect CA-path metadata appropriately. Prefer absolute or `~/...` credential paths; other relative credential paths resolve from the launching working directory. Existing installed launcher copies must be reviewed/reinstalled manually; they are not updated by this commit.

Precedence: explicit CLI > environment > TOML > safe defaults. `DRONK_CONFIG` (or `--config`) selects the private TOML path; otherwise `~/.config/dronk/config.toml`, respecting XDG_CONFIG_HOME. If no file exists, default mode is CAPTURE with no configured receivers or forwarding destination. The console always starts host monitoring STOPPED; no laptop boot autostart is installed.

| Variable | Meaning |
| --- | --- |
| `DRONK_MODE` | DEMO, CAPTURE or LIVE |
| `DRONK_TAK_HOST` | TLS certificate hostname, independent of network route |
| `DRONK_TAK_PORT` | TLS port, default 8089 |
| `DRONK_TAK_CONNECT_ADDRESS` | Optional network destination; empty defaults to TLS hostname |
| `DRONK_TAK_CLIENT_P12` | Protected PKCS#12 file path |
| `DRONK_TAK_CA_FILE` | Explicit CA bundle path |
| `DRONK_TAK_PASSWORD_FILE` | Protected UTF-8 password file path |

No plaintext password override is supported. Unknown TOML keys and a plaintext password setting reject without echoing supplied values. Configuration errors and host/JVM startup errors withhold private paths/details. Certificate-loading errors do not dump stack traces into dashboard logs. The Python DRONK supervisor and legacy adapter load TOML; direct JVM invocation accepts the same TAK environment variables/explicit flags, but does not parse TOML itself.

## Mode boundaries

- **DEMO:** stored receiver settings are ignored as of Phase 1.1; explicit physical receiver arguments or forwarding are rejected. The JVM receives a synthetic source flag and cannot instantiate the live transport. Demo observations stay synthetic and do not cross to TAK.
- **CAPTURE:** physical receivers are enabled only after manual Start; TAK forwarding is disabled even when private TAK settings are present. Conflicting forwarding requests reject.
- **LIVE:** physical receiver configuration, certificate hostname, PKCS#12, password-file and CA-file references are required before the monitoring worker is started. TLS credentials are loaded only for the live transport. Missing settings fail closed. Credential metadata/readability and private PKCS#12/password-file permissions are checked before starting the worker. The worker also waits for the bridge readiness event (after local credential loading) before opening USB, so invalid client/CA data cannot race a serial-port open.

Start/Stop still controls the actual laptop worker and its USB/TLS lifetime. Stopping it never commands SUSPECTRUM to stop. Firmware autonomous startup, bounded output, watchdog/reset telemetry and fixed passive channel behavior are unchanged. Stream/session counters, disconnect handling and OUI-SPY provisional association remain.

For local demo, use `bash tools/dronk/run-dronk.sh --demo`. For capture, use `--mode CAPTURE` and explicit stream/device settings. For an operator-authorized live session after migration, select `--mode LIVE`, configure physical receivers, open the localhost console and manually Start. These are instructions, not actions performed during remediation.

## TLS identity and routing

The underlying TCP socket connects to `connect_address` (or the certificate hostname by default). The TLS socket is layered with the certificate hostname as its logical peer; DNS SNI and HTTPS endpoint identification remain enabled, with explicit CA trust, TLS 1.2+ and client certificate authentication. The alternate address cannot bypass certificate identity validation. See [JSSE socket layering](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/javax/net/ssl/SSLSocketFactory.html#createSocket(java.net.Socket,java.lang.String,int,boolean)).

Local TLS tests use temporary synthetic certificates and loopback only: a localhost certificate succeeds while routing to the loopback IP; a different certificate hostname is rejected even with the correct CA and connection address. No production handshake was attempted. The old synthetic forwarding runtime flag now rejects to enforce DEMO isolation. The historical headless network diagnostic requires explicit LIVE mode and credentials; it is not DRONK DEMO and must not be run without authorization.

## Receiver identity privacy

Raw stable receiver IDs remain internal in the hub for expected-ID association, collision checks and session replay protection. Public labels are independent. Configure `[receiver_aliases] front = "north"`, or `--receiver-alias front=north`. Without an override, the stream label is public. Labels must be unique and must not embed a MAC/factory receiver ID.

CoT's receiverId extension, Linux observation metadata, receiver cards, logs and detection exports use the public label. Internal `DronkReception.receiverId` and snapshots retain raw IDs; public serialization substitutes aliases. Typed aircraft UID and duplicate suppression do not depend on receiver alias. Legacy UID namespaces remain internal and stable; changing configured stream names or an explicit legacy namespace can change provisional UIDs. This is exposure minimization, not authentication or full anonymization. Aircraft broadcast identifiers and transmitter metadata remain unverified observation data. Private config/raw captures/process inspection can still reveal association values to local OS users; do not publish those artifacts.

## Tracked-tree sanitization and release blockers

Personal endpoints, network addresses, hardware-specific serial defaults, credential locations, absolute owner home paths and username examples were removed or replaced with documented safe examples. Historical connection evidence was rewritten as generic procedures rather than falsely claiming the example server was tested. Standards service hashes, upstream copyright/license data, synthetic locally administered fixture MACs and software version numbers are preserved.

`tools/security/check_public_tree.py` scans tracked working-tree text (or staged text with `--index`), emitting only file/line/category counts. Its optional `--private-values-file` takes a protected inventory outside Git; values are never printed. The local exact-match inventory and scan output are not committed. This targeted check is not a dedicated secret scanner, does not inspect all history and is not publication approval.

Remaining public-release gates:

1. **Existing Git history and author/committer metadata still contain private metadata.** No history/ref/identity rewrite was authorized or performed. Do not publish this repository/history as-is. A separately authorized sanitized export/new-history process and publication identity review remain necessary.
2. Run independent dedicated secret/history scanners and manual full-tree review on the actual proposed publication artifact. The private audit report, local configs, captures, installed launcher copies, scanner inventory/output and unrelated untracked notes must stay out.
3. External copies, build logs, shell history, chat/tool output and prior exports were not sanitized by changing tracked files. Review them separately before sharing. Inspection output during this session is not a public artifact.
4. Real mutual-TLS connectivity with migrated settings and hardware/USB/RF gates remain operator validation tasks. No live server, credential, or hardware change was needed to preserve the integration path.

Regression checks cover mode isolation, configuration precedence/incompleteness, private error redaction, actual localhost TLS hostname validation, receiver aliases without identity/dedup changes, host Start/Stop and reconnect behavior, the legacy adapter, and existing firmware sanitizers. See the commit summary for final counts.

## Phase 1 validation results

Passed **166 automated tests**: 77 Android/core (including real localhost mutual-TLS hostname/address tests), 20 Linux bridge, 29 DRONK configuration/control/PTY/privacy/scanner tests, 10 legacy adapter and 30 firmware host tests with ASan/UBSan mutation coverage. Desktop and iPhone-sized Chromium checks passed for manual Start/Stop, logs, exports and disconnect behavior. The legacy Java diagnostic compiled and launcher shell/Python syntax checks passed. Firmware source and build configuration are unchanged from `9c406fd`; its channel 6/149 build results remain recorded in the preflight report. No hardware was opened or flashed and no production TAK endpoint was contacted.

The migrated local references pass file metadata/permission preflight without reading credential contents. Real routing, client-auth and application receipt with that configuration remain manual validation. The tracked working/staged source checks use a private exact-match inventory plus generic metadata rules, not full historical secret-scanner coverage; a zero finding count does not remove the release gates above.

## Phase 1.1 audit fixes (`1786cc1`)

Phase 1 is committed at `e72b06e`; the recovery/bring-up guides now use the post-security commands. Phase 1.1 fixes the legacy log fallback to expand `$HOME`, ignores runtime logs and isolates all Python/Gradle test environments from operator configuration. Its full run passed 181 tests plus isolated desktop/mobile browser checks and fresh channel 6/149 firmware builds, without production TAK or physical receiver access.

Both Linux paths now use a bounded 64-entry sender queue and a 10-second deadline. Overflow drops the oldest pending event; expired and shutdown events have separate counters. The underlying TCP socket is closed before TLS cleanup so a stalled SSL write cannot hold serial ingestion's thread or its Stop path. No timed-out event is replayed, and stream writes still do not acknowledge application receipt. The cancellation behavior follows the [Socket close contract](https://docs.oracle.com/en/java/javase/17/docs/api/java.base/java/net/Socket.html#close()) and is covered by a real stalled loopback TLS test. Safe startup/runtime error categories provide actions without paths or private exception details.

Linux pilot/takeoff-location forwarding is controlled by `[tak] forward_pilot_location`, `DRONK_TAK_FORWARD_PILOT_LOCATION=true|false` and the CLI `--forward-pilot-location` / `--no-forward-pilot-location`. New configs and the public example default to false. The active private config was migrated to explicit true, preserving its prior behavior; a mode-0600 backup remains outside Git, and all other settings are unchanged. Local decoding, tracking and exports never discard independently valid operator data because of this forwarding policy. The Android prototype's existing behavior is unchanged.

See [recovery](DRONK-RECOVERY.md) for current software commands/counters and [C5 bring-up](C5-HARDWARE-BRINGUP.md) for Friday's manual hardware gates. Public-release blockers above remain; no push, history rewrite or server change was performed.
