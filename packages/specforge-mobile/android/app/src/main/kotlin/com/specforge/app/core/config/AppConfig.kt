package com.specforge.app.core.config

import com.specforge.app.BuildConfig

/** Параметры сборки. Значения задаются в gradle.properties (specforge.*), не в коде. */
data class AppConfig(
    val apiBaseUrl: String,
    val mockRoute: Boolean,
    val timeScale: Int,
    val debug: Boolean,
) {
    companion object {
        fun fromBuild() = AppConfig(
            apiBaseUrl = BuildConfig.API_BASE_URL,
            mockRoute = BuildConfig.MOCK_ROUTE,
            timeScale = BuildConfig.TIME_SCALE.coerceAtLeast(1),
            debug = BuildConfig.DEBUG,
        )
    }
}
