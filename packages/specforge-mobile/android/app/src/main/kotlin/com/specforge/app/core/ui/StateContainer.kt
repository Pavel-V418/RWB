package com.specforge.app.core.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp

/**
 * Обёртка экрана: рисует loading/empty/error/forbidden единообразно и ставит testTag
 * по соглашению с участником 4: "<screenId>" на корне, "<screenId>-state-<state>" на состоянии,
 * "<screenId>-retry" на кнопке повтора. Агент использует её в каждом экране.
 */
@Composable
fun <T> StateContainer(
    screenId: String,
    state: UiState<T>,
    onRetry: () -> Unit,
    modifier: Modifier = Modifier,
    content: @Composable (T) -> Unit,
) {
    Box(modifier.fillMaxSize().testTag(screenId)) {
        when (state) {
            is UiState.Ready -> content(state.data)
            UiState.Loading -> Box(
                Modifier.fillMaxSize().testTag("$screenId-state-loading"),
                contentAlignment = Alignment.Center,
            ) { CircularProgressIndicator() }
            is UiState.Empty -> Message(screenId, "empty", state.title, state.text, null)
            is UiState.Error -> Message(screenId, "error", state.title, state.text, if (state.canRetry) onRetry else null)
            is UiState.Forbidden -> Message(screenId, "forbidden", state.title, state.text, null)
        }
    }
}

@Composable
private fun Message(screenId: String, stateName: String, title: String, text: String, onRetry: (() -> Unit)?) {
    Column(
        Modifier.fillMaxSize().padding(24.dp).testTag("$screenId-state-$stateName"),
        verticalArrangement = Arrangement.spacedBy(12.dp, Alignment.CenterVertically),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(title, style = MaterialTheme.typography.titleLarge, textAlign = TextAlign.Center)
        Text(text, style = MaterialTheme.typography.bodyMedium, textAlign = TextAlign.Center)
        if (onRetry != null) {
            Button(onClick = onRetry, modifier = Modifier.testTag("$screenId-retry")) { Text("Повторить") }
        }
    }
}
