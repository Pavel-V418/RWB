package com.specforge.app.feature

import com.specforge.app.core.di.AppContainer
import com.specforge.app.core.tracking.PositionSink
import com.specforge.app.feature.session.ApiSessionRepository
import com.specforge.app.feature.session.SessionRepository
import com.specforge.app.feature.wiring.GeneratedApi
import java.util.UUID

/**
 * Контракт ядра с feature-слоем (обязателен в любом кейсе):
 *   - FeatureModule(container) с полем positionSink;
 *   - @Composable AppRoot(container) — корень навигации.
 * Всё остальное в feature/ агент пишет под конкретное ТЗ.
 */
class FeatureModule(container: AppContainer) {
    private val api = GeneratedApi(container.config.apiBaseUrl, container.okHttp)

    val sessions: ApiSessionRepository = ApiSessionRepository(
        api = api,
        tokenStore = container.tokenStore,
        vehicleId = UUID.fromString(DEMO_VEHICLE_ID),
        mockSource = container.config.mockRoute,
    )
    val repository: SessionRepository get() = sessions
    val positionSink: PositionSink get() = sessions

    private companion object {
        // Машина водителя из сида стенда (участник 3). TODO: брать из профиля пользователя, когда появится в API.
        const val DEMO_VEHICLE_ID = "0192f000-0000-7000-8000-00000000de01"
    }
}
