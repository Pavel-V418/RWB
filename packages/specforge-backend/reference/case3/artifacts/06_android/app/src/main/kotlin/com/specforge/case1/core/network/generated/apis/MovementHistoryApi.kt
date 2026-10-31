package com.specforge.case1.core.network.generated.apis

import com.specforge.case1.core.network.generated.infrastructure.CollectionFormats.*
import retrofit2.http.*
import retrofit2.Response
import okhttp3.RequestBody
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

import com.specforge.case1.core.network.generated.models.GetToolMovementHistory200Response
import com.specforge.case1.core.network.generated.models.Problem

interface MovementHistoryApi {
    /**
     * GET api/v1/tools/{id}/movements
     * Получить историю движения инструмента
     * Роли: warehouse_manager, warehouse_keeper. Требования: FR-9.
     * Responses:
     *  - 200: Успешно
     *  - 400: Ошибка валидации запроса
     *  - 401: Нет или истёк токен доступа
     *  - 403: Недостаточно прав для операции
     *  - 404: Ресурс не найден
     *  - 500: Внутренняя ошибка
     *
     * @param id 
     * @param idempotencyKey ключ идемпотентности; повтор с тем же ключом возвращает первый результат
     * @param page  (optional)
     * @param pageSize  (optional)
     * @param page2  (optional, default to 1)
     * @param pageSize2  (optional, default to 50)
     * @return [GetToolMovementHistory200Response]
     */
    @GET("api/v1/tools/{id}/movements")
    suspend fun getToolMovementHistory(@Path("id") id: java.util.UUID, @Header("Idempotency-Key") idempotencyKey: java.util.UUID, @Query("page") page: kotlin.Int? = null, @Query("pageSize") pageSize: kotlin.Int? = null, @Query("page") page2: kotlin.Int? = 1, @Query("pageSize") pageSize2: kotlin.Int? = 50): Response<GetToolMovementHistory200Response>

}
