package com.specforge.app.core.auth

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Хранит токен доступа. SharedPreferences в режиме PRIVATE: доступны только этому приложению,
 * allowBackup=false в манифесте, поэтому токен не уходит в резервные копии.
 */
class TokenStore(context: Context) {
    private val prefs = context.getSharedPreferences("auth", Context.MODE_PRIVATE)
    private val _token = MutableStateFlow(prefs.getString(KEY, null))
    val token: StateFlow<String?> = _token.asStateFlow()

    fun set(value: String?) {
        prefs.edit().apply { if (value == null) remove(KEY) else putString(KEY, value) }.apply()
        _token.value = value
    }

    private companion object {
        const val KEY = "access_token"
    }
}
