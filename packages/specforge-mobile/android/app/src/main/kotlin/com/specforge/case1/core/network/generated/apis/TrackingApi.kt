package com.specforge.case1.core.network.generated.apis

import com.specforge.case1.core.network.generated.infrastructure.CollectionFormats.*
import retrofit2.http.*
import retrofit2.Response
import okhttp3.RequestBody
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

import com.specforge.case1.core.network.generated.models.Problem
import com.specforge.case1.core.network.generated.models.ReportPositionsRequest
import com.specforge.case1.core.network.generated.models.ReportPositionsResponse
import com.specforge.case1.core.network.generated.models.RouteFeature
import com.specforge.case1.core.network.generated.models.ValidationProblem

interface TrackingApi {
    /**
     * GET api/v1/vehicles/{vehicleId}/route
     * Маршрут автомобиля за период (GeoJSON)
     * Точки трека за период в виде GeoJSON LineString с временем каждой вершины.
     * Responses:
     *  - 200: Маршрут
     *  - 400: Ошибка валидации
     *  - 401: Нет или неверный токен
     *  - 403: Недостаточно прав
     *  - 404: Не найдено
     *  - 429: Превышен лимит запросов
     *  - 500: Внутренняя ошибка сервиса; детали только в логах по traceId
     *
     * @param vehicleId 
     * @param from Начало периода (включительно) (optional)
     * @param to Конец периода (не включительно) (optional)
     * @return [RouteFeature]
     */
    @GET("api/v1/vehicles/{vehicleId}/route")
    suspend fun getVehicleRoute(@Path("vehicleId") vehicleId: java.util.UUID, @Query("from") from: java.time.OffsetDateTime? = null, @Query("to") to: java.time.OffsetDateTime? = null): Response<RouteFeature>

    /**
     * POST api/v1/vehicles/{vehicleId}/positions
     * Передать GPS-точки (пакетом) и получить текущий статус
     * Точки обрабатываются по возрастанию &#x60;recordedAt&#x60;. Для каждой точки определяется зона, переходы (вход, выход, смена зоны) порождают события и уведомления. Повторная отправка точки с тем же &#x60;recordedAt&#x60; игнорируется (идемпотентно). 
     * Responses:
     *  - 200: Обработано
     *  - 400: Ошибка валидации
     *  - 401: Нет или неверный токен
     *  - 403: Недостаточно прав
     *  - 404: Не найдено
     *  - 429: Превышен лимит запросов
     *  - 500: Внутренняя ошибка сервиса; детали только в логах по traceId
     *
     * @param vehicleId 
     * @param reportPositionsRequest 
     * @return [ReportPositionsResponse]
     */
    @POST("api/v1/vehicles/{vehicleId}/positions")
    suspend fun reportPositions(@Path("vehicleId") vehicleId: java.util.UUID, @Body reportPositionsRequest: ReportPositionsRequest): Response<ReportPositionsResponse>

}
