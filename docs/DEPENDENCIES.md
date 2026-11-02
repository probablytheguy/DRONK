# Dependency and license review

Original OUI-TAK code is licensed Apache-2.0. No upstream firmware or ATAK code/binaries are linked or redistributed. Primary source research links do not grant redistribution rights.

Resolved application runtime dependencies: Kotlin standard library 2.2.10 (automatically supplied by AGP built-in Kotlin), Apache-2.0, and its transitive JetBrains annotations 13.0, Apache-2.0. Android UI and framework APIs come from the target Android platform; this application has no third-party UI, USB, TAK, or network library.

Test dependency: JUnit 4.13.2 (EPL-1.0), with Hamcrest Core 1.3 (BSD-3-Clause); these are test-only and are not packaged into the APK. SQLite uses Android framework APIs, with no Room or other new runtime dependency. Android instrumentation uses the framework runner/base/mock libraries as test-only shared libraries; no ATAK library is linked. Build tooling includes Android Gradle Plugin 9.1.1 (Apache-2.0), Gradle 9.3.1 (Apache-2.0), and their transitive tooling dependencies. The standard Gradle wrapper scripts/JAR retain their upstream license notices. Android SDK components are locally installed under their own terms and are not redistributed.

This is a baseline review, not a legal opinion or an exhaustive transitive SBOM. Before publishing binary releases, review resolved runtime dependencies and required notices again; before adding adapters, review every new library and any copied protocol definition. Apache-2.0 here covers original project work, not third-party dependencies under their own licenses.

Inspect resolved dependencies with:

```sh
./gradlew :app:dependencies --configuration debugRuntimeClasspath
./gradlew :app:dependencies --configuration debugUnitTestRuntimeClasspath
```
