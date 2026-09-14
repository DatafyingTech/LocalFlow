plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Release signing. The permanent key lives outside the repo; CI decodes it from the
// ANDROID_KEYSTORE_B64 secret and a local build points ANDROID_KEYSTORE_FILE at the .jks.
// Each value comes from an environment variable, falling back to a Gradle property of the
// same name (-PANDROID_KEY_ALIAS=... or ~/.gradle/gradle.properties). Never commit any of them.
fun signingValue(name: String): String? =
    System.getenv(name)?.takeIf { it.isNotBlank() }
        ?: (project.findProperty(name) as String?)?.takeIf { it.isNotBlank() }

val releaseKeystoreFile = signingValue("ANDROID_KEYSTORE_FILE")?.let { file(it) }
val releaseKeystorePassword = signingValue("ANDROID_KEYSTORE_PASSWORD")
val releaseKeyAlias = signingValue("ANDROID_KEY_ALIAS")
val releaseKeyPassword = signingValue("ANDROID_KEY_PASSWORD")
val releaseSigningAvailable =
    releaseKeystoreFile?.isFile == true &&
        releaseKeystorePassword != null && releaseKeyAlias != null && releaseKeyPassword != null

android {
    namespace = "tech.datafying.localflow"
    compileSdk = 35

    defaultConfig {
        applicationId = "tech.datafying.localflow"
        minSdk = 26
        targetSdk = 35
        versionCode = 5
        versionName = "0.1.4"
    }

    signingConfigs {
        create("release") {
            if (releaseSigningAvailable) {
                storeFile = releaseKeystoreFile
                storePassword = releaseKeystorePassword
                keyAlias = releaseKeyAlias
                keyPassword = releaseKeyPassword
            }
        }
    }

    buildTypes {
        debug {
            // Local development build, signed with the machine's auto-generated debug key.
            // It is NOT what the GitHub release ships (see the release build type).
            isMinifyEnabled = false
        }
        release {
            // What the GitHub release ships. Signed with the one permanent LocalFlow key so a
            // new version installs over the old one. Not shrunk yet: keep stack traces readable.
            isMinifyEnabled = false
            isDebuggable = false
            if (releaseSigningAvailable) {
                signingConfig = signingConfigs.getByName("release")
            } else {
                logger.warn(
                    "LocalFlow: release signing not configured (ANDROID_KEYSTORE_FILE / " +
                        "ANDROID_KEYSTORE_PASSWORD / ANDROID_KEY_ALIAS / ANDROID_KEY_PASSWORD " +
                        "missing) - assembleRelease will produce an UNSIGNED APK."
                )
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
        buildConfig = true
    }

    lint {
        // Lint runs in CI for the report; warnings must not turn the build red.
        abortOnError = false
        checkReleaseBuilds = false
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    testImplementation("junit:junit:4.13.2")
}
