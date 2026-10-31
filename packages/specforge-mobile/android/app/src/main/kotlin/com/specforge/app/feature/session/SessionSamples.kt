package com.specforge.app.feature.session

import androidx.compose.runtime.Composable
import com.specforge.app.core.ui.UiState

/** Примерные данные экрана — для снимков и тестов. */
object SessionSamples {
    val ZONE = ZoneInfo("6f1c1e64-0000-4000-8000-000000000001", "Казанская пл. дом 2", "360 ₽/час", 15, 15)
    val SESSION = ActiveSession("7a1c1e64-0000-4000-8000-000000000002", "Казанская пл. дом 2", "10:25", 1500)
    val STATUS_IN_ZONE = LiveStatus("А001АА178", ZONE, null)
    val STATUS_WITH_SESSION = LiveStatus("А001АА178", ZONE, SESSION)
}

/** Экран в состоянии из UX-спеки — по нему генерируется скриншот-тест (tools/android_gate/screens.py). */
@Composable
fun SessionScreenSample(state: String) {
    val ui: UiState<SessionScreenData> = when (state) {
        "ready" -> UiState.Ready(SessionScreenData(SessionSamples.STATUS_WITH_SESSION, remainingSeconds = 540, tracking = true))
        "loading" -> UiState.Loading
        "empty" -> UiState.Empty("Автомобиль не найден", "К вашей учётной записи не привязан автомобиль")
        "error" -> UiState.Error("Не удалось загрузить данные", "Нет связи с сервером")
        "forbidden" -> UiState.Forbidden()
        else -> error("Нет примера для состояния $state")
    }
    SessionScreen(ui, {}, {}, {}, {})
}
