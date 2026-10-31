package com.specforge.case1.core.network.generated.apis

import com.specforge.case1.core.network.generated.infrastructure.CollectionFormats.*
import retrofit2.http.*
import retrofit2.Response
import okhttp3.RequestBody
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

import com.specforge.case1.core.network.generated.models.ListReturnRecords200Response
import com.specforge.case1.core.network.generated.models.Problem
import com.specforge.case1.core.network.generated.models.ReturnRecord
import com.specforge.case1.core.network.generated.models.ReturnToolRequest

interface ReturnRecordsApi {

    /**
    * enum for parameter condition
    */
    @Serializable
    enum class ConditionListReturnRecords(val value: kotlin.String) {
        @SerialName(value = "working") WORKING("working"),
        @SerialName(value = "needs_repair") NEEDS_REPAIR("needs_repair"),
        @SerialName(value = "lost") LOST("lost")
    }

    /**
     * GET api/v1/return-records
     * Получить список записей о возврате
     * Роли: warehouse_manager, warehouse_keeper. Требования: FR-6.
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
     * @param condition  (optional)
     * @param page  (optional)
     * @param pageSize  (optional)
     * @param page2  (optional, default to 1)
     * @param pageSize2  (optional, default to 50)
     * @return [ListReturnRecords200Response]
     */
    @GET("api/v1/return-records")
    suspend fun listReturnRecords(@Header("Idempotency-Key") idempotencyKey: java.util.UUID, @Query("toolId") toolId: java.util.UUID? = null, @Query("employeeId") employeeId: java.util.UUID? = null, @Query("condition") condition: ConditionListReturnRecords? = null, @Query("page") page: kotlin.Int? = null, @Query("pageSize") pageSize: kotlin.Int? = null, @Query("page") page2: kotlin.Int? = 1, @Query("pageSize") pageSize2: kotlin.Int? = 50): Response<ListReturnRecords200Response>

    /**
     * POST api/v1/return-records
     * Зафиксировать возврат инструмента
     * Роли: warehouse_keeper. Требования: FR-6, FR-17.
     * Responses:
     *  - 201: Успешно
     *  - 400: Ошибка валидации запроса
     *  - 401: Нет или истёк токен доступа
     *  - 403: Недостаточно прав для операции
     *  - 404: Ресурс не найден
     *  - 409: Конфликт состояния ресурса
     *  - 422: Нарушено бизнес-правило
     *  - 429: Превышен лимит запросов
     *  - 500: Внутренняя ошибка
     *
     * @param returnToolRequest 
     * @return [ReturnRecord]
     */
    @POST("api/v1/return-records")
    suspend fun returnTool(@Body returnToolRequest: ReturnToolRequest): Response<ReturnRecord>

}
