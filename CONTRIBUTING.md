# Contributing

Discuss broad architecture changes before implementation. Keep adapters and serialization independent of Android UI. Add tests for behavioral changes to validation, normalization, and CoT generation.

Before proposing a change:

```sh
./gradlew :app:assembleDebug :app:testDebugUnitTest :app:lintDebug
```

Exercise the UI on an emulator, including generate/start/stop/clear and foreground/background behavior. Use synthetic fixtures only. Do not commit credentials, certificates, device identifiers, private location histories, logs with private data, generated artifacts, IDE state, or local SDK paths. `.gitignore` is a convenience, not a substitute for reviewing the diff.

Hardware adapters require pinned upstream source evidence, documented framing and units, synthetic fixtures, malformed-input tests, and verification against the intended firmware/device. Do not infer wire protocols from the normalized model. ATAK delivery claims require evidence of actual ingestion on the tested ATAK version and configured input. Keep firmware and ATAK binaries out of this repository.

New dependencies require version and license review, including runtime/transitive dependencies. Contributions are submitted under Apache-2.0. For security reports, follow [SECURITY.md](SECURITY.md).
