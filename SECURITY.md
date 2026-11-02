# Security policy

DRONK is experimental software. There are no supported releases yet; fixes go into the main branch.

## Reporting a vulnerability

Please report security problems privately through GitHub's **Report a vulnerability** button (Security → Advisories) on this repository. Do not open a public issue for them.

Do not include secrets, TAK certificates, passwords, real aircraft serial numbers, captures, or real locations of aircraft, operators or receivers in a report. Synthetic examples are enough. If private data is needed to reproduce a problem, say so and we'll arrange a private channel first.

## Scope

In scope:

- Parsing of receiver serial output and Remote ID payloads (firmware and host), including malformed or hostile broadcasts.
- The local Command Center (it binds to `127.0.0.1` by default; exposing it on a network is not supported).
- TAK forwarding: TLS configuration, certificate and hostname verification, credential handling.
- Leaks of private data (operator positions, credentials, device identifiers) into logs, CoT or files.

Out of scope: the security of Remote ID itself, TAK Server or TAK client software, and the ESP-IDF platform. Report those upstream.

## Design notes

- DRONK is receive-only. The firmware has no RF transmit path and no serial command interpreter.
- TAK forwarding uses mutual TLS and always verifies the server certificate and hostname. There is no option to disable verification.
- Credentials are read from files outside the repository; passwords are never accepted as command-line arguments.
- Captures and the database are stored with owner-only permissions under `~/.local/state/dronk`.
