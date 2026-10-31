package com.specforge.app.feature.session

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.specforge.app.core.network.ApiResult
import com.specforge.app.core.time.countdown
import com.specforge.app.core.ui.UiState
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.filterNotNull
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.util.UUID

class SessionViewModel(
    private val repo: SessionRepository,
    private val timeScale: Int,
    tracking: Flow<Boolean>,
    private val newKey: () -> String = { UUID.randomUUID().toString() },
) : ViewModel() {

    private val _state = MutableStateFlow<UiState<SessionScreenData>>(UiState.Loading)
    val state: StateFlow<UiState<SessionScreenData>> = _state.asStateFlow()

    private var countdownJob: Job? = null
    private var trackingOn = false

    init {
        load()
        viewModelScope.launch { repo.live.filterNotNull().collect(::applyStatus) }
        viewModelScope.launch {
            tracking.collect { on ->
                trackingOn = on
                updateReady { it.copy(tracking = on) }
            }
        }
    }

    fun load() {
        _state.value = UiState.Loading
        viewModelScope.launch {
            when (val r = repo.refresh()) {
                is ApiResult.Ok -> applyStatus(r.value)
                is ApiResult.Fail -> _state.value = when (r.code) {
                    403 -> UiState.Forbidden()
                    404 -> UiState.Empty("Автомобиль не найден", "К вашей учётной записи не привязан автомобиль")
                    else -> UiState.Error("Не удалось загрузить данные", r.message)
                }
            }
        }
    }

    fun start(minutes: Int) {
        val zone = (state.value as? UiState.Ready)?.data?.status?.zone ?: return
        runAction("Сессия начата") { repo.start(zone.id, minutes, newKey()) }
    }

    fun extend(minutes: Int) {
        val session = (state.value as? UiState.Ready)?.data?.status?.session ?: return
        runAction("Сессия продлена на $minutes мин") { repo.extend(session.id, minutes, newKey()) }
    }

    private fun runAction(okMessage: String, call: suspend () -> ApiResult<ActiveSession>) {
        if ((state.value as? UiState.Ready)?.data?.busy == true) return   // защита от двойного нажатия
        updateReady { it.copy(busy = true, message = null) }
        viewModelScope.launch {
            when (val r = call()) {
                is ApiResult.Ok -> updateReady { d ->
                    d.copy(busy = false, message = okMessage, status = d.status.copy(session = r.value))
                }.also { restartCountdown(r.value.remainingSeconds) }
                is ApiResult.Fail -> updateReady { it.copy(busy = false, message = r.message) }
            }
        }
    }

    private fun applyStatus(status: LiveStatus) {
        val current = (_state.value as? UiState.Ready)?.data
        _state.value = UiState.Ready(
            SessionScreenData(
                status = status,
                remainingSeconds = status.session?.remainingSeconds,
                tracking = trackingOn,
                busy = current?.busy ?: false,
                message = current?.message,
            )
        )
        restartCountdown(status.session?.remainingSeconds)
    }

    private fun restartCountdown(remaining: Long?) {
        countdownJob?.cancel()
        if (remaining == null) return
        countdownJob = viewModelScope.launch {
            countdown(remaining, timeScale).collect { left -> updateReady { it.copy(remainingSeconds = left) } }
        }
    }

    private fun updateReady(block: (SessionScreenData) -> SessionScreenData) {
        _state.update { s -> if (s is UiState.Ready) UiState.Ready(block(s.data)) else s }
    }

    companion object {
        /** Порог предупреждения на экране — как у backend (за 10 минут). */
        const val WARN_SECONDS = 600L
    }
}
