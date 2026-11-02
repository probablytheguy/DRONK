plugins { id("com.android.application") }
android {
    namespace = "org.ouitak.app"
    compileSdk = 37
    defaultConfig {
        applicationId = "org.ouitak.app"
        minSdk = 26
        testInstrumentationRunner = "android.test.InstrumentationTestRunner"
        targetSdk = 37
        versionCode = 4
        versionName = "0.4.0"
    }
    useLibrary("android.test.runner")
    useLibrary("android.test.base")
    useLibrary("android.test.mock")
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}
dependencies {
    implementation(project(":shared-core"))
    testImplementation("junit:junit:4.13.2")
}
