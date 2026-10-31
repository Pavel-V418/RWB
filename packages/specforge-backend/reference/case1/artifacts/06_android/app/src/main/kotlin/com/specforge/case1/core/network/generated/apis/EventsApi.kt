package com.specforge.case1.core.network.generated.apis

import com.specforge.case1.core.network.generated.infrastructure.CollectionFormats.*
import retrofit2.http.*
import retrofit2.Response
import okhttp3.RequestBody
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

import com.specforge.case1.core.network.generated.models.DomainEventPage
import com.specforge.case1.core.network.generated.models.EventType
import com.specforge.case1.core.network.generated.models.Problem
import com.specforge.case1.core.network.generated.models.ValidationProblem

interface EventsApi {
    /**
     * GET api/v1/events
     * Журнал доменных событий (admin)
     * Журнал событий для административного интерфейса, сортировка по occurredAt по убыванию.
     * Responses:
     *  - 200: Страница событий
     *  - 400: Ошибка валидации
     *  - 401: Нет или неверный токен
     *  - 403: Недостаточно прав
     *  - 429: Превышен лимит запросов
     *  - 500: Внутренняя ошибка сервиса; детали только в логах по traceId
     *
     * @param vehicleId  (optional)
     * @param type  (optional)
     * @param from Начало периода (включительно) (optional)
     * @param to Конец периода (не включительно) (optional)
     * @param page  (optional, default to 1)
     * @param pageSize  (optional, default to 50)
     * @return [DomainEventPage]
     */
    @GET("api/v1/events")
    suspend fun listEvents(@Query("vehicleId") vehicleId: java.util.UUID? = null, @Query("type") type: EventType? = null, @Query("from") from: java.time.OffsetDateTime? = null, @Query("to") to: java.time.OffsetDateTime? = null, @Query("page") page: kotlin.Int? = 1, @Query("pageSize") pageSize: kotlin.Int? = 50): Response<DomainEventPage>

}
