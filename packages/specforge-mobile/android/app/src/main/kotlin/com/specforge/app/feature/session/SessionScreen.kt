package com.specforge.app.feature.session

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import com.specforge.app.R
import com.specforge.app.core.time.formatDuration
import com.specforge.app.core.ui.StateContainer
import com.specforge.app.core.ui.UiState
import com.specforge.app.feature.Screens

@Composable
fun SessionRoute(vm: SessionViewModel, onStartTracking: () -> Unit, onStopTracking: () -> Unit) {
    val state by vm.state.collectAsState()
    SessionScreen(
        state = state,
        onRetry = vm::load,
        onStart = vm::start,
        onExtend = vm::extend,
        onTracking = { on -> if (on) onStartTracking() else onStopTracking() },
    )
}

/** Stateless-экран: его же рендерит скриншот-тест во всех состояниях. */
@Composable
fun SessionScreen(
    state: UiState<SessionScreenData>,
    onRetry: () -> Unit,
    onStart: (Int) -> Unit,
    onExtend: (Int) -> Unit,
    onTracking: (Boolean) -> Unit,
) {
    Scaffold { padding ->
        StateContainer(Screens.SESSION, state, onRetry, Modifier.padding(padding)) { d ->
            Column(
                Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                TrackingCard(d, onTracking)
                ZoneCard(d)
                val session = d.status.session
                if (session != null) {
                    SessionCard(session, d.remainingSeconds ?: session.remainingSeconds, d.busy,
                        extendMinutes = d.status.zone?.stepMinutes?.takeIf { it > 0 } ?: DEFAULT_MINUTES, onExtend)
                } else if (d.status.zone != null) {
                    StartCard(d.status.zone, d.busy, onStart)
                }
                d.message?.let {
                    Text(it, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.testTag("session-message"))
                }
            }
        }
    }
}

@Composable
private fun TrackingCard(d: SessionScreenData, onTracking: (Boolean) -> Unit) = Card(Modifier.fillMaxWidth()) {
    Row(Modifier.padding(16.dp).fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(d.status.vehicleTitle ?: stringResource(R.string.session_vehicle_unknown), style = MaterialTheme.typography.titleMedium)
            Text(stringResource(if (d.tracking) R.string.session_tracking_on else R.string.session_tracking_off),
                style = MaterialTheme.typography.bodySmall)
        }
        Switch(checked = d.tracking, onCheckedChange = onTracking, modifier = Modifier.testTag("session-tracking-toggle"))
    }
}

@Composable
private fun ZoneCard(d: SessionScreenData) = Card(Modifier.fillMaxWidth().testTag("session-zone")) {
    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        val zone = d.status.zone
        if (zone == null) {
            Text(stringResource(R.string.session_no_zone), style = MaterialTheme.typography.titleMedium)
        } else {
            Text(stringResource(R.string.session_zone_label), style = MaterialTheme.typography.labelMedium)
            Text(zone.title, style = MaterialTheme.typography.titleMedium)
            zone.priceText?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
        }
    }
}

@Composable
private fun SessionCard(session: ActiveSession, remaining: Long, busy: Boolean, extendMinutes: Int, onExtend: (Int) -> Unit) =
    Card(Modifier.fillMaxWidth().testTag("session-active")) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(stringResource(R.string.session_paid_until, session.paidUntilText), style = MaterialTheme.typography.titleMedium)
            Text(formatDuration(remaining), style = MaterialTheme.typography.displayMedium, modifier = Modifier.testTag("session-countdown"))
            if (remaining in 1..SessionViewModel.WARN_SECONDS) {
                Text(stringResource(R.string.session_expiring_warning), color = MaterialTheme.colorScheme.error,
                    modifier = Modifier.testTag("session-expiring-warning"))
            }
            Button(onClick = { onExtend(extendMinutes) }, enabled = !busy, modifier = Modifier.fillMaxWidth().testTag("session-extend")) {
                Text(stringResource(R.string.session_extend, extendMinutes))
            }
        }
    }

@Composable
private fun StartCard(zone: ZoneInfo, busy: Boolean, onStart: (Int) -> Unit) = Card(Modifier.fillMaxWidth()) {
    val min = zone.minMinutes.coerceAtLeast(1)
    val step = zone.stepMinutes.coerceAtLeast(1)
    val options = listOf(min, min + step, min + 3 * step).distinct()
    var selected by remember(zone.id) { mutableIntStateOf(options.first()) }
    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(stringResource(R.string.session_choose_duration), style = MaterialTheme.typography.titleMedium)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            options.forEach { m ->
                FilterChip(selected = m == selected, onClick = { selected = m },
                    label = { Text(stringResource(R.string.session_minutes, m)) },
                    modifier = Modifier.testTag("session-duration-$m"))
            }
        }
        Button(onClick = { onStart(selected) }, enabled = !busy, modifier = Modifier.fillMaxWidth().testTag("session-start")) {
            Text(stringResource(R.string.session_start))
        }
        OutlinedButton(onClick = {}, enabled = false, modifier = Modifier.fillMaxWidth()) {
            Text(stringResource(R.string.session_pay_hint))
        }
    }
}

private const val DEFAULT_MINUTES = 15
