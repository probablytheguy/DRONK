# Discovery preservation and privacy review

Reports were copied from /tmp/ouitak05-discovery before implementation. The device USB serial and local home directory were replaced with placeholders. Original raw capture, capture metadata, and executable capture script are deliberately not included; originals remain in /tmp. Downloaded third-party references are linked rather than vendored.

The 1,282-byte transcript was reviewed: boot banners, ROM addresses, diagnostics and a malformed status message only; no aircraft identifiers, MAC addresses, coordinates, credentials, SSIDs or device USB serial appear. It is preserved as app/src/test/resources/serial/captured-boot.sanitized.txt with original bytes/line endings. ROM/build addresses are firmware diagnostics, not personal identifiers. Reports describe credential-related source code but contain no actual credential values.

No synthetic detection is presented as hardware evidence. The original RESULTS report is historical; SOURCE-INVESTIGATION corrects its claim about receive-buffer flushing and qualifies erasure success. Host device serial placeholders are not usable device paths.
