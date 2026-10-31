package com.specforge.case1.core.network.generated.apis

import com.specforge.case1.core.network.generated.infrastructure.CollectionFormats.*
import retrofit2.http.*
import retrofit2.Response
import okhttp3.RequestBody
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

import com.specforge.case1.core.network.generated.models.CreateToolRequest
import com.specforge.case1.core.network.generated.models.ListTools200Response
import com.specforge.case1.core.network.generated.models.Problem
import com.specforge.case1.core.network.generated.models.Tool

interface ToolsApi {
    /**
     * POST api/v1/tools
     * Добавить инструмент в каталог
     * Роли: warehouse_manager. Требования: FR-1, FR-2, FR-12.
     * Responses:
     *  - 201: Успешно
     *  - 400: Ошибка валидации запроса
     *  - 401: Нет или истёк токен доступа
     *  - 403: Недостаточно прав для операции
     *  - 409: Конфликт состояния ресурса
     *  - 422: Нарушено бизнес-правило
     *  - 500: Внутренняя ошибка
     *
     * @param createToolRequest 
     * @return [Tool]
     */
    @POST("api/v1/tools")
    suspend fun createTool(@Body createToolRequest: CreateToolRequest): Response<Tool>

    /**
     * GET api/v1/tools/{id}
     * Получить инструмент по ID
     * Роли: warehouse_manager, warehouse_keeper, employee. Требования: FR-1.
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
     * @return [Tool]
     */
    @GET("api/v1/tools/{id}")
    suspend fun getTool(@Path("id") id: java.util.UUID, @Header("Idempotency-Key") idempotencyKey: java.util.UUID): Response<Tool>

    /**
     * GET api/v1/tools/qr/{qrCode}
     * Получить инструмент по QR-коду
     * Роли: warehouse_keeper. Требования: FR-3.
     * Responses:
     *  - 200: Успешно
     *  - 400: Ошибка валидации запроса
     *  - 401: Нет или истёк токен доступа
     *  - 403: Недостаточно прав для операции
     *  - 404: Ресурс не найден
     *  - 429: Превышен лимит запросов
     *  - 500: Внутренняя ошибка
     *
     * @param qrCode 
     * @param idempotencyKey ключ идемпотентности; повтор с тем же ключом возвращает первый результат
     * @return [Tool]
     */
    @GET("api/v1/tools/qr/{qrCode}")
    suspend fun getToolByQrCode(@Path("qrCode") qrCode: kotlin.String, @Header("Idempotency-Key") idempotencyKey: java.util.UUID): Response<Tool>


    /**
    * enum for parameter status
    */
    @Serializable
    enum class StatusListTools(val value: kotlin.String) {
        @SerialName(value = "available") AVAILABLE("available"),
        @SerialName(value = "issued") ISSUED("issued"),
        @SerialName(value = "reserved") RESERVED("reserved"),
        @SerialName(value = "repair") REPAIR("repair"),
        @SerialName(value = "lost") LOST("lost"),
        @SerialName(value = "retired") RETIRED("retired")
    }

    /**
     * GET api/v1/tools
     * Получить список инструментов
     * Роли: warehouse_manager, warehouse_keeper, employee. Требования: FR-1.
     * Responses:
     *  - 200: Успешно
     *  - 400: Ошибка валидации запроса
     *  - 401: Нет или истёк токен доступа
     *  - 403: Недостаточно прав для операции
     *  - 500: Внутренняя ошибка
     *
     * @param idempotencyKey ключ идемпотентности; повтор с тем же ключом возвращает первый результат
     * @param category  (optional)
     * @param warehouse  (optional)
     * @param status  (optional)
     * @param page Номер страницы (optional)
     * @param pageSize Размер страницы (optional)
     * @param page2  (optional, default to 1)
     * @param pageSize2  (optional, default to 50)
     * @return [ListTools200Response]
     */
    @GET("api/v1/tools")
    suspend fun listTools(@Header("Idempotency-Key") idempotencyKey: java.util.UUID, @Query("category") category: kotlin.String? = null, @Query("warehouse") warehouse: kotlin.String? = null, @Query("status") status: StatusListTools? = null, @Query("page") page: kotlin.Int? = null, @Query("pageSize") pageSize: kotlin.Int? = null, @Query("page") page2: kotlin.Int? = 1, @Query("pageSize") pageSize2: kotlin.Int? = 50): Response<ListTools200Response>

    /**
     * POST api/v1/tools/{id}/retirements
     * Списать инструмент
     * Роли: warehouse_manager. Требования: FR-8.
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
     * @return [Tool]
     */
    @POST("api/v1/tools/{id}/retirements")
    suspend fun retireTool(@Path("id") id: java.util.UUID, @Header("Idempotency-Key") idempotencyKey: java.util.UUID): Response<Tool>

}
