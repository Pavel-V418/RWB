package com.specforge.case1.core.network.generated.apis

import com.specforge.case1.core.network.generated.infrastructure.CollectionFormats.*
import retrofit2.http.*
import retrofit2.Response
import okhttp3.RequestBody
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

import com.specforge.case1.core.network.generated.models.ExtendSessionRequest
import com.specforge.case1.core.network.generated.models.ParkingSession
import com.specforge.case1.core.network.generated.models.ParkingSessionPage
import com.specforge.case1.core.network.generated.models.Problem
import com.specforge.case1.core.network.generated.models.SessionStatus
import com.specforge.case1.core.network.generated.models.StartSessionRequest
import com.specforge.case1.core.network.generated.models.ValidationProblem

interface SessionsApi {
    /**
     * POST api/v1/sessions/{sessionId}/closure
     * Завершить сессию вручную
     * Водитель завершает сессию досрочно. Повторный вызов с тем же Idempotency-Key безопасен.
     * Responses:
     *  - 200: Завершена
     *  - 401: Нет или неверный токен
     *  - 403: Недостаточно прав
     *  - 404: Не найдено
     *  - 409: Конфликт состояния (например, уже есть активная сессия)
     *  - 429: Превышен лимит запросов
     *  - 500: Внутренняя ошибка сервиса; детали только в логах по traceId
     *
     * @param sessionId 
     * @param idempotencyKey Уникальный ключ операции; повтор с тем же ключом и телом вернёт исходный ответ
     * @return [ParkingSession]
     */
    @POST("api/v1/sessions/{sessionId}/closure")
    suspend fun closeSession(@Path("sessionId") sessionId: java.util.UUID, @Header("Idempotency-Key") idempotencyKey: kotlin.String): Response<ParkingSession>

    /**
     * POST api/v1/sessions/{sessionId}/extensions
     * Продлить активную сессию (создать продление)
     * Продлевает оплату активной сессии через адаптер провайдера. Переносит paidUntil и сбрасывает предупреждение.
     * Responses:
     *  - 200: Продлена
     *  - 400: Ошибка валидации
     *  - 401: Нет или неверный токен
     *  - 403: Недостаточно прав
     *  - 404: Не найдено
     *  - 409: Конфликт состояния (например, уже есть активная сессия)
     *  - 502: Ошибка внешнего провайдера оплаты
     *  - 429: Превышен лимит запросов
     *  - 500: Внутренняя ошибка сервиса; детали только в логах по traceId
     *
     * @param sessionId 
     * @param idempotencyKey Уникальный ключ операции; повтор с тем же ключом и телом вернёт исходный ответ
     * @param extendSessionRequest 
     * @return [ParkingSession]
     */
    @POST("api/v1/sessions/{sessionId}/extensions")
    suspend fun extendSession(@Path("sessionId") sessionId: java.util.UUID, @Header("Idempotency-Key") idempotencyKey: kotlin.String, @Body extendSessionRequest: ExtendSessionRequest): Response<ParkingSession>

    /**
     * GET api/v1/vehicles/{vehicleId}/active-session
     * Активная парковочная сессия автомобиля
     * Текущая активная сессия автомобиля или 204, если её нет.
     * Responses:
     *  - 200: Активная сессия
     *  - 204: Активной сессии нет
     *  - 401: Нет или неверный токен
     *  - 403: Недостаточно прав
     *  - 404: Не найдено
     *  - 429: Превышен лимит запросов
     *  - 500: Внутренняя ошибка сервиса; детали только в логах по traceId
     *
     * @param vehicleId 
     * @return [ParkingSession]
     */
    @GET("api/v1/vehicles/{vehicleId}/active-session")
    suspend fun getActiveSession(@Path("vehicleId") vehicleId: java.util.UUID): Response<ParkingSession>

    /**
     * GET api/v1/sessions/{sessionId}
     * Сессия
     * Сессия по идентификатору.
     * Responses:
     *  - 200: Сессия
     *  - 401: Нет или неверный токен
     *  - 403: Недостаточно прав
     *  - 404: Не найдено
     *  - 429: Превышен лимит запросов
     *  - 500: Внутренняя ошибка сервиса; детали только в логах по traceId
     *
     * @param sessionId 
     * @return [ParkingSession]
     */
    @GET("api/v1/sessions/{sessionId}")
    suspend fun getSession(@Path("sessionId") sessionId: java.util.UUID): Response<ParkingSession>

    /**
     * GET api/v1/sessions
     * История парковочных сессий
     * История сессий с фильтрами по автомобилю, статусу и периоду. Сортировка по startedAt по убыванию.
     * Responses:
     *  - 200: Страница сессий
     *  - 400: Ошибка валидации
     *  - 401: Нет или неверный токен
     *  - 429: Превышен лимит запросов
     *  - 500: Внутренняя ошибка сервиса; детали только в логах по traceId
     *
     * @param vehicleId  (optional)
     * @param status  (optional)
     * @param from Начало периода (включительно) (optional)
     * @param to Конец периода (не включительно) (optional)
     * @param page  (optional, default to 1)
     * @param pageSize  (optional, default to 50)
     * @return [ParkingSessionPage]
     */
    @GET("api/v1/sessions")
    suspend fun listSessions(@Query("vehicleId") vehicleId: java.util.UUID? = null, @Query("status") status: SessionStatus? = null, @Query("from") from: java.time.OffsetDateTime? = null, @Query("to") to: java.time.OffsetDateTime? = null, @Query("page") page: kotlin.Int? = 1, @Query("pageSize") pageSize: kotlin.Int? = 50): Response<ParkingSessionPage>

    /**
     * POST api/v1/sessions
     * Начать (подтвердить) парковочную сессию
     * Оплата выполняется через адаптер провайдера (в прототипе — mock «Паркоматики»). Длительность не меньше &#x60;minTimeMinutes&#x60; зоны и кратна &#x60;timeStepMinutes&#x60;. У автомобиля может быть только одна активная сессия. 
     * Responses:
     *  - 201: Сессия создана
     *  - 400: Ошибка валидации
     *  - 401: Нет или неверный токен
     *  - 403: Недостаточно прав
     *  - 404: Не найдено
     *  - 409: Конфликт состояния (например, уже есть активная сессия)
     *  - 422: Нарушено бизнес-правило (например, длительность меньше минимальной)
     *  - 502: Ошибка внешнего провайдера оплаты
     *  - 429: Превышен лимит запросов
     *  - 500: Внутренняя ошибка сервиса; детали только в логах по traceId
     *
     * @param idempotencyKey Уникальный ключ операции; повтор с тем же ключом и телом вернёт исходный ответ
     * @param startSessionRequest 
     * @return [ParkingSession]
     */
    @POST("api/v1/sessions")
    suspend fun startSession(@Header("Idempotency-Key") idempotencyKey: kotlin.String, @Body startSessionRequest: StartSessionRequest): Response<ParkingSession>

}
