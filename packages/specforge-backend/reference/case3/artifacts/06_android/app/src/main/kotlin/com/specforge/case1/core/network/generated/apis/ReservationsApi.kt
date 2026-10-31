package com.specforge.case1.core.network.generated.apis

import com.specforge.case1.core.network.generated.infrastructure.CollectionFormats.*
import retrofit2.http.*
import retrofit2.Response
import okhttp3.RequestBody
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

import com.specforge.case1.core.network.generated.models.CreateReservationRequest
import com.specforge.case1.core.network.generated.models.ListReservations200Response
import com.specforge.case1.core.network.generated.models.Problem
import com.specforge.case1.core.network.generated.models.Reservation

interface ReservationsApi {
    /**
     * POST api/v1/reservations/{id}/cancellations
     * Отменить бронирование
     * Роли: employee, warehouse_manager. Требования: FR-4.
     * Responses:
     *  - 200: Успешно
     *  - 400: Ошибка валидации запроса
     *  - 401: Нет или истёк токен доступа
     *  - 403: Недостаточно прав для операции
     *  - 404: Ресурс не найден
     *  - 409: Конфликт состояния ресурса
     *  - 500: Внутренняя ошибка
     *
     * @param id 
     * @param idempotencyKey ключ идемпотентности; повтор с тем же ключом возвращает первый результат
     * @return [Reservation]
     */
    @POST("api/v1/reservations/{id}/cancellations")
    suspend fun cancelReservation(@Path("id") id: java.util.UUID, @Header("Idempotency-Key") idempotencyKey: java.util.UUID): Response<Reservation>

    /**
     * POST api/v1/reservations
     * Забронировать инструмент на дату
     * Роли: employee, warehouse_manager. Требования: FR-4, FR-13.
     * Responses:
     *  - 201: Успешно
     *  - 400: Ошибка валидации запроса
     *  - 401: Нет или истёк токен доступа
     *  - 403: Недостаточно прав для операции
     *  - 404: Ресурс не найден
     *  - 409: Конфликт состояния ресурса
     *  - 422: Нарушено бизнес-правило
     *  - 500: Внутренняя ошибка
     *
     * @param idempotencyKey ключ идемпотентности; повтор с тем же ключом возвращает первый результат
     * @param createReservationRequest 
     * @return [Reservation]
     */
    @POST("api/v1/reservations")
    suspend fun createReservation(@Header("Idempotency-Key") idempotencyKey: java.util.UUID, @Body createReservationRequest: CreateReservationRequest): Response<Reservation>


    /**
    * enum for parameter status
    */
    @Serializable
    enum class StatusListReservations(val value: kotlin.String) {
        @SerialName(value = "active") ACTIVE("active"),
        @SerialName(value = "cancelled") CANCELLED("cancelled"),
        @SerialName(value = "fulfilled") FULFILLED("fulfilled")
    }

    /**
     * GET api/v1/reservations
     * Получить список бронирований
     * Роли: warehouse_manager, warehouse_keeper, employee. Требования: FR-4.
     * Responses:
     *  - 200: Успешно
     *  - 400: Ошибка валидации запроса
     *  - 401: Нет или истёк токен доступа
     *  - 403: Недостаточно прав для операции
     *  - 500: Внутренняя ошибка
     *
     * @param idempotencyKey ключ идемпотентности; повтор с тем же ключом возвращает первый результат
     * @param toolId  (optional)
     * @param employeeId  (optional)
     * @param date  (optional)
     * @param status  (optional)
     * @param page  (optional)
     * @param pageSize  (optional)
     * @param page2  (optional, default to 1)
     * @param pageSize2  (optional, default to 50)
     * @return [ListReservations200Response]
     */
    @GET("api/v1/reservations")
    suspend fun listReservations(@Header("Idempotency-Key") idempotencyKey: java.util.UUID, @Query("toolId") toolId: java.util.UUID? = null, @Query("employeeId") employeeId: java.util.UUID? = null, @Query("date") date: java.time.LocalDate? = null, @Query("status") status: StatusListReservations? = null, @Query("page") page: kotlin.Int? = null, @Query("pageSize") pageSize: kotlin.Int? = null, @Query("page") page2: kotlin.Int? = 1, @Query("pageSize") pageSize2: kotlin.Int? = 50): Response<ListReservations200Response>

}
