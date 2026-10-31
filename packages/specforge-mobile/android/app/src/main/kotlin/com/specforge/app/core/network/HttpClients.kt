package com.specforge.app.core.network

import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import java.util.concurrent.TimeUnit

object HttpClients {
    /**
     * Общий OkHttp: подставляет Bearer-токен, в debug логирует только строки запросов
     * (BASIC, без тел — чтобы токены и пароли не попадали в logcat).
     */
    fun okHttp(tokenProvider: () -> String?, debug: Boolean): OkHttpClient =
        OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(20, TimeUnit.SECONDS)
            .addInterceptor { chain ->
                val request = chain.request()
                val token = tokenProvider()
                val authorized = if (token != null && request.header("Authorization") == null) {
                    request.newBuilder().header("Authorization", "Bearer $token").build()
                } else {
                    request
                }
                chain.proceed(authorized)
            }
            .apply {
                if (debug) addInterceptor(HttpLoggingInterceptor().apply { level = HttpLoggingInterceptor.Level.BASIC })
            }
            .build()
}

/** Результат вызова API без исключений — удобно для UiState. */
sealed interface ApiResult<out T> {
    data class Ok<T>(val value: T) : ApiResult<T>
    data class Fail(val code: Int?, val message: String) : ApiResult<Nothing>
}

/** Обёртка над suspend-вызовом Retrofit, возвращающим Response<T>. */
suspend fun <T> apiCall(block: suspend () -> retrofit2.Response<T>): ApiResult<T> = try {
    val response = block()
    val body = response.body()
    when {
        response.isSuccessful && body != null -> ApiResult.Ok(body)
        response.isSuccessful -> ApiResult.Fail(response.code(), "Пустой ответ сервера")
        response.code() == 401 -> ApiResult.Fail(401, "Требуется вход")
        response.code() == 403 -> ApiResult.Fail(403, "Нет доступа")
        else -> ApiResult.Fail(response.code(), "Ошибка сервера (${response.code()})")
    }
} catch (e: java.io.IOException) {
    ApiResult.Fail(null, "Нет связи с сервером")
} catch (e: kotlinx.coroutines.CancellationException) {
    throw e
} catch (e: Exception) {
    ApiResult.Fail(null, "Что-то пошло не так")
}
