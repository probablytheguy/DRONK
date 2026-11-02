# Remote TAK integration (sanitized notes)

The existing integration uses TAK legacy XML CoT over authenticated TLS with a client PKCS#12 and an explicit CA bundle. A successful stream write is not TAK application receipt.

Personal DNS names, public/private network destinations and client credential locations were removed from the tracked tree. Historical connectivity evidence is private and does not establish connectivity to any publication example. It is intentionally not reproduced here.

See [Phase 1 configuration and migration](../DRONK-SECURITY-PHASE-1.md). Configure the certificate DNS name separately from an optional network connection address. The logical TLS peer/SNI remains the certificate name; HTTPS endpoint identification and CA validation are never disabled. Paths refer to local protected files; no password value belongs in a command, environment variable, document, or Git commit.

The existing server and credentials were not modified, rotated, deleted, or contacted during this remediation. Local migration preserves former settings outside Git. Authorized live validation remains a manual operator step after selecting physical receivers and LIVE mode.
