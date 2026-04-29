import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.hilt.android)
    alias(libs.plugins.ksp)
}

// Google OAuth 2.0 Web Client ID is resolved at build time from (in priority order):
//   1. GOOGLE_WEB_CLIENT_ID environment variable (CI / release builds)
//   2. android/local.properties key `GOOGLE_WEB_CLIENT_ID` (developer machines)
//   3. Empty string (runtime code detects this and surfaces a clear config error)
// It is intentionally never committed to VCS.
val googleWebClientId: String = run {
    val fromEnv = System.getenv("GOOGLE_WEB_CLIENT_ID")
    if (!fromEnv.isNullOrBlank()) return@run fromEnv
    val localPropsFile = rootProject.file("local.properties")
    if (localPropsFile.exists()) {
        val props = Properties().apply { localPropsFile.inputStream().use { load(it) } }
        props.getProperty("GOOGLE_WEB_CLIENT_ID", "").orEmpty()
    } else {
        ""
    }
}

// Central Server base URL is resolved at build time from (in priority order):
//   1. CENTRAL_SERVER_URL environment variable (CI / release builds)
//   2. android/local.properties key `CENTRAL_SERVER_URL` (developer machines)
//   3. Production default.
val centralServerUrl: String = run {
    val fromEnv = System.getenv("CENTRAL_SERVER_URL")
    if (!fromEnv.isNullOrBlank()) return@run fromEnv
    val localPropsFile = rootProject.file("local.properties")
    if (localPropsFile.exists()) {
        val props = Properties().apply { localPropsFile.inputStream().use { load(it) } }
        val fromProps = props.getProperty("CENTRAL_SERVER_URL", "").orEmpty()
        if (fromProps.isNotBlank()) return@run fromProps
    }
    "https://api.mobaide.com"
}

// Release signing config is resolved at build time from (in priority order):
//   1. Environment variables (CI / release builds)
//   2. android/signing.properties (developer machines; git-ignored)
//   3. null — release variant falls back to debug signing for local-only builds
val releaseSigningProps: Properties? = run {
    val f = rootProject.file("signing.properties")
    if (f.exists()) {
        Properties().apply { f.inputStream().use { load(it) } }
    } else {
        null
    }
}

fun signingValue(envName: String, propName: String): String? {
    val fromEnv = System.getenv(envName)
    if (!fromEnv.isNullOrBlank()) return fromEnv
    val fromProps = releaseSigningProps?.getProperty(propName)
    return if (fromProps.isNullOrBlank()) null else fromProps
}

android {
    namespace = "com.mobaide.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.mobaide.app"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.0"

        buildConfigField("String", "CENTRAL_SERVER_URL", "\"$centralServerUrl\"")
        buildConfigField("String", "GOOGLE_WEB_CLIENT_ID", "\"$googleWebClientId\"")
        buildConfigField("int", "APP_PROTOCOL_VERSION", "1")
        buildConfigField("int", "MIN_SERVER_PROTOCOL_VERSION", "1")
    }

    signingConfigs {
        create("release") {
            val storeFilePath = signingValue("ANDROID_KEYSTORE_PATH", "storeFile")
            val storePassword = signingValue("ANDROID_KEYSTORE_PASSWORD", "storePassword")
            val keyAlias = signingValue("ANDROID_KEY_ALIAS", "keyAlias")
            val keyPassword = signingValue("ANDROID_KEY_PASSWORD", "keyPassword")
            if (storeFilePath != null && storePassword != null && keyAlias != null && keyPassword != null) {
                this.storeFile = rootProject.file(storeFilePath)
                this.storePassword = storePassword
                this.keyAlias = keyAlias
                this.keyPassword = keyPassword
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
            // Use the release config when fully configured; otherwise fall back
            // to debug so `assembleRelease` keeps working for local-only smoke
            // tests. CI builds always export the env vars and get a real
            // upload-key signature.
            val releaseConfig = signingConfigs.getByName("release")
            signingConfig = if (releaseConfig.storeFile != null) {
                releaseConfig
            } else {
                signingConfigs.getByName("debug")
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

dependencies {
    // Core
    implementation(libs.androidx.core.ktx)
    implementation(libs.kotlinx.coroutines.android)

    // Lifecycle
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)

    // Compose
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.material.icons.extended)
    implementation(libs.androidx.activity.compose)
    debugImplementation(libs.androidx.compose.ui.tooling)

    // Navigation
    implementation(libs.androidx.navigation.compose)

    // Hilt
    implementation(libs.hilt.android)
    ksp(libs.hilt.compiler)
    implementation(libs.hilt.navigation.compose)

    // Networking
    implementation(libs.okhttp)
    implementation(libs.kotlinx.serialization.json)

    // Security
    implementation(libs.androidx.security.crypto)

    // DataStore (last-used project / session persistence)
    implementation(libs.androidx.datastore.preferences)

    // Google Credential Manager (Sign-In with Google)
    implementation(libs.androidx.credentials)
    implementation(libs.androidx.credentials.play.services)
    implementation(libs.google.googleid)

    // Vendored Termux terminal renderer (Apache-2.0)
    implementation(project(":terminal-view"))

    // Drag-and-drop reordering in Compose LazyColumn (MIT, Calvin-LL/Reorderable)
    implementation(libs.reorderable)

    // Google Play in-app updates (flexible + immediate flows)
    implementation(libs.play.app.update.ktx)

    // Unit testing
    testImplementation(libs.junit)
    testImplementation(libs.mockk)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation(libs.turbine)
}
