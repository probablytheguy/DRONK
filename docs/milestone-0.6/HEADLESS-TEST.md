# Manual headless TAK diagnostic (sanitized procedure)

Historical private destination and credential metadata have been removed. The hostnames and addresses in publication examples are placeholders; no connectivity to those examples is claimed.

For routine operation, use [DRONK private configuration](../DRONK-SECURITY-PHASE-1.md). DEMO never connects to TAK. CAPTURE never forwards.

`HeadlessTakIntegration.java` remains an explicitly networked diagnostic for an operator-authorized LIVE environment. It requires `DRONK_MODE=LIVE`, an explicit certificate hostname, protected client PKCS#12/password-file/CA paths and optional separate connect address. It has no personal destination defaults. It does not read a plaintext password environment variable. Never run it against a server without authorization.

A schematic environment uses `DRONK_TAK_HOST=tak.example.invalid` and, when required, `DRONK_TAK_CONNECT_ADDRESS=192.0.2.20`; substitute actual settings locally, never in this document. Supply paths through `DRONK_TAK_CLIENT_P12`, `DRONK_TAK_PASSWORD_FILE` and `DRONK_TAK_CA_FILE`. Keep all actual values and credential files outside Git.

The tool's `--preflight` option performs a real mutual-TLS handshake but sends no CoT. Other modes send explicitly synthetic CoT over the configured TLS connection. Neither is part of DRONK DEMO mode. No production server was contacted during Phase 1 remediation. Certificate trust and hostname validation must stay enabled, including when routing to an alternate network address.
