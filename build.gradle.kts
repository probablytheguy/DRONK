plugins { id("com.android.application") version "9.1.1" apply false }

// Unit-test subprocesses must never inherit an operator's live DRONK settings.
subprojects {
    tasks.withType<org.gradle.api.tasks.testing.Test>().configureEach {
        setEnvironment(environment.filterKeys { !it.startsWith("DRONK_") })
        val isolatedHome = layout.buildDirectory.dir("isolated-test-home")
        systemProperty("user.home", isolatedHome.get().asFile.absolutePath)
        doFirst { isolatedHome.get().asFile.mkdirs() }
    }
}
