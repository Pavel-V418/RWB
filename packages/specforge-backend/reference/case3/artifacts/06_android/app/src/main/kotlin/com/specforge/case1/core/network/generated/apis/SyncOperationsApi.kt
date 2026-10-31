package com.specforge.case1.core.network.generated.apis

import com.specforge.case1.core.network.generated.infrastructure.CollectionFormats.*
import retrofit2.http.*
import retrofit2.Response
import okhttp3.RequestBody
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

import com.specforge.case1.core.network.generated.models.Problem
import com.specforge.case1.core.network.generated.models.SyncOperationsRequest

interface SyncOperationsApi {
    /**
     * POST api/v1/synchronization-operations
     * Синхронизировать локальные операции с сервером
     * Роли: warehouse_keeper. Требования: FR-10, FR-15.
     * Responses:
     *  - 200: Успешно
     *  - 400: Ошибка валидации запроса
     *  - 401: Нет или истёк токен доступа
     *  - 403: Недостаточно прав для операции
     *  - 422: Нарушено бизнес-правило
     *  - 500: Внутренняя ошибка
     *
     * @param idempotencyKey ключ идемпотентности; повтор с тем же ключом возвращает первый результат
     * @param syncOperationsRequest 
     * @return [kotlinx.serialization.json.JsonObject]
     */
    @POST("api/v1/synchronization-operations")
    suspend fun syncOperations(@Header("Idempotency-Key") idempotencyKey: java.util.UUID, @Body syncOperationsRequest: SyncOperationsRequest): Response<kotlinx.serialization.json.JsonObject>

}
