package com.specforge.case1.core.network.generated.apis

import com.specforge.case1.core.network.generated.infrastructure.CollectionFormats.*
import retrofit2.http.*
import retrofit2.Response
import okhttp3.RequestBody
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

import com.specforge.case1.core.network.generated.models.LoginRequest
import com.specforge.case1.core.network.generated.models.LoginResponse
import com.specforge.case1.core.network.generated.models.Problem
import com.specforge.case1.core.network.generated.models.User
import com.specforge.case1.core.network.generated.models.ValidationProblem

interface AuthApi {
    /**
     * GET api/v1/auth/me
     * Текущий пользователь
     * Профиль владельца токена.
     * Responses:
     *  - 200: Профиль
     *  - 401: Нет или неверный токен
     *  - 429: Превышен лимит запросов
     *  - 500: Внутренняя ошибка сервиса; детали только в логах по traceId
     *
     * @return [User]
     */
    @GET("api/v1/auth/me")
    suspend fun getCurrentUser(): Response<User>

    /**
     * POST api/v1/auth/tokens
     * Выпустить JWT по логину и паролю
     * Проверяет логин и пароль, возвращает JWT с ролью пользователя. Ограничен по частоте запросов.
     * Responses:
     *  - 200: Токен выдан
     *  - 400: Ошибка валидации
     *  - 401: Нет или неверный токен
     *  - 429: Превышен лимит запросов
     *  - 500: Внутренняя ошибка сервиса; детали только в логах по traceId
     *
     * @param loginRequest 
     * @return [LoginResponse]
     */
    @POST("api/v1/auth/tokens")
    suspend fun login(@Body loginRequest: LoginRequest): Response<LoginResponse>

}
