package com.specforge.app.core.ui

/**
 * Состояния экрана — те же пять, что в UX-спеке участника 4:
 * ready, loading, empty, error, forbidden.
 */
sealed interface UiState<out T> {
    data object Loading : UiState<Nothing>
    data class Ready<T>(val data: T) : UiState<T>
    data class Empty(val title: String, val text: String) : UiState<Nothing>
    data class Error(val title: String, val text: String, val canRetry: Boolean = true) : UiState<Nothing>
    data class Forbidden(val title: String = "Нет доступа", val text: String = "У вас нет прав на этот раздел") : UiState<Nothing>

    /** Имя состояния для testTag и имени файла скриншота. */
    val stateName: String
        get() = when (this) {
            Loading -> "loading"
            is Ready -> "ready"
            is Empty -> "empty"
            is Error -> "error"
            is Forbidden -> "forbidden"
        }
}
