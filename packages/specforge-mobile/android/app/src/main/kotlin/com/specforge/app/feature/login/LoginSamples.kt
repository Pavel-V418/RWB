package com.specforge.app.feature.login

import androidx.compose.runtime.Composable

/** Экран в состоянии из UX-спеки — по нему генерируется скриншот-тест (tools/android_gate/screens.py). */
@Composable
fun LoginScreenSample(state: String) {
    val ui = when (state) {
        "ready" -> LoginUiState(login = "driver.demo")
        "error" -> LoginUiState(login = "driver.demo", error = "Неверный логин или пароль")
        else -> error("Нет примера для состояния $state")
    }
    LoginScreen(ui, {}, {}, {})
}
