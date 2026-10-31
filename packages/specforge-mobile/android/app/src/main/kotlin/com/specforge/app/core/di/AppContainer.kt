package com.specforge.app.core.di

import android.content.Context
import com.specforge.app.core.auth.TokenStore
import com.specforge.app.core.config.AppConfig
import com.specforge.app.core.location.FusedLocationSource
import com.specforge.app.core.location.LocationSource
import com.specforge.app.core.location.MockRouteLocationSource
import com.specforge.app.core.network.HttpClients
import com.specforge.app.core.tracking.Notifier
import com.specforge.app.feature.FeatureModule
import okhttp3.OkHttpClient

/**
 * Ручной DI вместо Hilt: меньше памяти и времени на сборку (kapt/ksp не нужны),
 * агенту проще — все зависимости видны в одном месте.
 * Точка входа в feature-слой — класс FeatureModule (его пишет агент под кейс).
 */
class AppContainer(val context: Context) {
    val config: AppConfig = AppConfig.fromBuild()
    val tokenStore = TokenStore(context)
    val notifier = Notifier(context)

    val okHttp: OkHttpClient by lazy { HttpClients.okHttp({ tokenStore.token.value }, config.debug) }

    val locationSource: LocationSource by lazy {
        if (config.mockRoute) {
            MockRouteLocationSource(
                readJson = { context.assets.open(MOCK_ROUTE_ASSET).bufferedReader().use { it.readText() } },
                timeScale = config.timeScale,
            )
        } else {
            FusedLocationSource(context)
        }
    }

    val feature: FeatureModule by lazy { FeatureModule(this) }

    companion object {
        const val MOCK_ROUTE_ASSET = "mock_route/points.json"
    }
}
