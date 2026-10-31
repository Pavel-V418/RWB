package com.specforge.case1.core.network.generated.apis

import com.specforge.case1.core.network.generated.infrastructure.CollectionFormats.*
import retrofit2.http.*
import retrofit2.Response
import okhttp3.RequestBody
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

import com.specforge.case1.core.network.generated.models.IssueRecord
import com.specforge.case1.core.network.generated.models.IssueToolRequest
import com.specforge.case1.core.network.generated.models.ListIssueRecords200Response
import com.specforge.case1.core.network.generated.models.Problem

interface IssueRecordsApi {
    /**
     * POST api/v1/issue-records
     * Выдать инструмент сотруднику
     * Роли: warehouse_keeper. Требования: FR-3, FR-5, FR-14.
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
     * @param issueToolRequest 
     * @return [IssueRecord]
     */
    @POST("api/v1/issue-records")
    suspend fun issueTool(@Body issueToolRequest: IssueToolRequest): Response<IssueRecord>


    /**
    * enum for parameter status
    */
    @Serializable
    enum class StatusListIssueRecords(val value: kotlin.String) {
        @SerialName(value = "active") ACTIVE("active"),
        @SerialName(value = "returned") RETURNED("returned"),
        @SerialName(value = "overdue") OVERDUE("overdue")
    }

    /**
     * GET api/v1/issue-records
     * Получить список записей о выдаче
     * Роли: warehouse_manager, warehouse_keeper. Требования: FR-7.
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
     * @param status  (optional)
     * @param overdueOnly Только невозвращённые с истёкшим сроком (optional)
     * @param page  (optional)
     * @param pageSize  (optional)
     * @param page2  (optional, default to 1)
     * @param pageSize2  (optional, default to 50)
     * @return [ListIssueRecords200Response]
     */
    @GET("api/v1/issue-records")
    suspend fun listIssueRecords(@Header("Idempotency-Key") idempotencyKey: java.util.UUID, @Query("toolId") toolId: java.util.UUID? = null, @Query("employeeId") employeeId: java.util.UUID? = null, @Query("status") status: StatusListIssueRecords? = null, @Query("overdueOnly") overdueOnly: kotlin.Boolean? = null, @Query("page") page: kotlin.Int? = null, @Query("pageSize") pageSize: kotlin.Int? = null, @Query("page") page2: kotlin.Int? = 1, @Query("pageSize") pageSize2: kotlin.Int? = 50): Response<ListIssueRecords200Response>

}
