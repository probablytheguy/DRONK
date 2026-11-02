# Third-party notices

Original DRONK code is licensed under the Apache License 2.0 ([LICENSE](LICENSE)). The components below are included in, or used to build or test, this repository under their own licenses. This is a good-faith inventory, not a legal opinion or a complete transitive SBOM; see [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md).

## Included in this repository

### OpenDroneID core-c

- **Files:** `firmware/esp32-c5-receiver/components/odid/vendor/opendroneid.c`, `opendroneid.h`
- **Source:** https://github.com/opendroneid/opendroneid-core-c, commit `849c03adbe0cdd68997a462fafab397edcfe53f3`, copied unchanged ([provenance](firmware/esp32-c5-receiver/components/odid/UPSTREAM.md))
- **Copyright:** (C) 2019 Intel Corporation and contributors, as stated in the source files
- **License:** Apache-2.0, full text in `firmware/esp32-c5-receiver/components/odid/vendor/LICENSE`. The upstream revision has no NOTICE file.

### Gradle Wrapper

- **Files:** `gradlew`, `gradlew.bat`, `gradle/wrapper/gradle-wrapper.jar`
- **Source:** Gradle 9.3.1. The JAR's SHA-256 matches the checksum Gradle publishes for that release.
- **Copyright:** the original authors, Gradle, Inc.
- **License:** Apache-2.0, notice retained in the scripts

## Used to build or test, not included

| Component | Version | License | Use |
|---|---|---|---|
| Kotlin standard library | 2.2.10 | Apache-2.0 | Runtime (Linux bridge, Android app) |
| JetBrains annotations | 13.0 | Apache-2.0 | Transitive of the Kotlin standard library |
| JUnit | 4.13.2 | EPL-1.0 | Tests only |
| Hamcrest Core | 1.3 | BSD-3-Clause | Tests only |
| Android Gradle Plugin | 9.1.1 | Apache-2.0 | Build only |
| Gradle | 9.3.1 | Apache-2.0 | Build only, downloaded by the wrapper |
| ESP-IDF | v5.5.4 | Apache-2.0 (components vary) | Firmware build; installed separately |
| Android SDK | platform 37 | Android SDK License | Android build; installed separately |
| Playwright for Python | any | Apache-2.0 | Optional browser check (`tools/dronk/tests/browser_check.py`) |

The Python host tools use only the Python standard library. No ATAK, iTAK or TAK Server code or binaries are included. Firmware binaries are not distributed.
