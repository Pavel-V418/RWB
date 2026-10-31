// ЗАМОРОЖЕНО для Android-агента: параметры кейса берутся из gradle.properties.
plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.roborazzi)
}

fun prop(name: String): String = providers.gradleProperty(name).get()

android {
    // namespace постоянный (код ядра), applicationId — от кейса
    namespace = "com.specforge.app"
    compileSdk = 36

    defaultConfig {
        applicationId = prop("specforge.appId")
        minSdk = 26          // java.time в сгенерированном клиенте без desugaring
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"

        resValue("string", "app_name", prop("specforge.appName"))
        buildConfigField("String", "API_BASE_URL", "\"${prop("specforge.apiBaseUrl")}\"")
        buildConfigField("boolean", "MOCK_ROUTE", prop("specforge.mockRoute"))
        buildConfigField("int", "TIME_SCALE", prop("specforge.timeScale"))
    }

    buildTypes {
        debug {
            manifestPlaceholders["networkConfig"] = "@xml/network_security_debug"
        }
        release {
            isMinifyEnabled = false
            manifestPlaceholders["networkConfig"] = "@xml/network_security_release"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    testOptions {
        unitTests {
            isIncludeAndroidResources = true
            all {
                it.maxHeapSize = "1024m"
                it.systemProperty("specforge.screenshotDir", rootProject.file("screenshots").absolutePath)
            }
        }
    }
    lint {
        abortOnError = false
    }
}

// Без jvmToolchain: собирается на любом JDK 17+ (у участников и на раннерах разные), байткод — 17.
kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.lifecycle.service)
    implementation(libs.androidx.navigation.compose)

    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.ui.tooling.preview)
    implementation(libs.compose.material3)
    implementation(libs.compose.material.icons)
    debugImplementation(libs.compose.ui.tooling)
    debugImplementation(libs.compose.ui.test.manifest)

    implementation(libs.play.services.location)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.kotlinx.coroutines.play.services)
    implementation(libs.kotlinx.serialization.json)

    // Сгенерированный клиент API (участник 3)
    implementation(libs.retrofit)
    implementation(libs.retrofit.kotlinx.serialization)
    implementation(libs.retrofit.scalars)
    implementation(libs.okhttp.logging)

    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation(libs.androidx.test.junit)
    testImplementation(libs.robolectric)
    testImplementation(platform(libs.compose.bom))
    testImplementation(libs.compose.ui.test.junit4)
    testImplementation(libs.roborazzi)
    testImplementation(libs.roborazzi.compose)
    testImplementation(libs.roborazzi.junit.rule)
}
