package com.specforge.case1.core.network.generated.apis

import com.specforge.case1.core.network.generated.infrastructure.CollectionFormats.*
import retrofit2.http.*
import retrofit2.Response
import okhttp3.RequestBody
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

import com.specforge.case1.core.network.generated.models.ListAuditLogs200Response
import com.specforge.case1.core.network.generated.models.Problem

interface AuditLogsApi {
    /**
     * GET api/v1/audit-logs
     * Получить журнал аудита
     * Роли: system_admin. Требования: FR-3, FR-4, FR-6, FR-11, FR-12, FR-13.
     * Responses:
     *  - 200: Успешно
     *  - 400: Ошибка валидации запроса
     *  - 401: Нет или истёк токен доступа
     *  - 403: Недостаточно прав для операции
     *  - 500: Внутренняя ошибка
     *
     * @param idempotencyKey ключ идемпотентности; повтор с тем же ключом возвращает первый результат
     * @param userId  (optional)
     * @param entityType  (optional)
     * @param from  (optional)
     * @param to  (optional)
     * @param page  (optional)
     * @param pageSize  (optional)
     * @param page2  (optional, default to 1)
     * @param pageSize2  (optional, default to 50)
     * @return [ListAuditLogs200Response]
     */
    @GET("api/v1/audit-logs")
    suspend fun listAuditLogs(@Header("Idempotency-Key") idempotencyKey: java.util.UUID, @Query("userId") userId: java.util.UUID? = null, @Query("entityType") entityType: kotlin.String? = null, @Query("from") from: java.time.OffsetDateTime? = null, @Query("to") to: java.time.OffsetDateTime? = null, @Query("page") page: kotlin.Int? = null, @Query("pageSize") pageSize: kotlin.Int? = null, @Query("page") page2: kotlin.Int? = 1, @Query("pageSize") pageSize2: kotlin.Int? = 50): Response<ListAuditLogs200Response>

}
