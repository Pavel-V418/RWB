package com.specforge.app.feature.login

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.specforge.app.core.network.ApiResult
import com.specforge.app.feature.session.SessionRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class LoginUiState(
    val login: String = "",
    val password: String = "",
    val busy: Boolean = false,
    val error: String? = null,
    val done: Boolean = false,
) {
    val canSubmit: Boolean get() = login.isNotBlank() && password.isNotBlank() && !busy
}

class LoginViewModel(private val repo: SessionRepository) : ViewModel() {
    private val _state = MutableStateFlow(LoginUiState())
    val state: StateFlow<LoginUiState> = _state.asStateFlow()

    fun onLogin(v: String) = _state.update { it.copy(login = v.trim(), error = null) }
    fun onPassword(v: String) = _state.update { it.copy(password = v, error = null) }

    fun submit() {
        val s = _state.value
        if (!s.canSubmit) return
        _state.update { it.copy(busy = true, error = null) }
        viewModelScope.launch {
            when (val r = repo.login(s.login, s.password)) {
                is ApiResult.Ok -> _state.update { it.copy(busy = false, done = true, password = "") }
                is ApiResult.Fail -> _state.update { it.copy(busy = false, error = r.message) }
            }
        }
    }
}
