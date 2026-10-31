package com.specforge.case1.core.network.generated.apis

import com.specforge.case1.core.network.generated.infrastructure.CollectionFormats.*
import retrofit2.http.*
import retrofit2.Response
import okhttp3.RequestBody
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

import com.specforge.case1.core.network.generated.models.CreateUserRequest
import com.specforge.case1.core.network.generated.models.ListUsers200Response
import com.specforge.case1.core.network.generated.models.Problem
import com.specforge.case1.core.network.generated.models.User

interface UsersApi {
    /**
     * POST api/v1/users
     * Создать пользователя системы
     * Роли: system_admin. Требования: FR-3, FR-4, FR-6, FR-11, FR-12, FR-13.
     * Responses:
     *  - 201: Успешно
     *  - 400: Ошибка валидации запроса
     *  - 401: Нет или истёк токен доступа
     *  - 403: Недостаточно прав для операции
     *  - 409: Конфликт состояния ресурса
     *  - 422: Нарушено бизнес-правило
     *  - 500: Внутренняя ошибка
     *
     * @param createUserRequest 
     * @return [User]
     */
    @POST("api/v1/users")
    suspend fun createUser(@Body createUserRequest: CreateUserRequest): Response<User>


    /**
    * enum for parameter role
    */
    @Serializable
    enum class RoleListUsers(val value: kotlin.String) {
        @SerialName(value = "warehouse_keeper") WAREHOUSE_KEEPER("warehouse_keeper"),
        @SerialName(value = "warehouse_manager") WAREHOUSE_MANAGER("warehouse_manager"),
        @SerialName(value = "employee") EMPLOYEE("employee"),
        @SerialName(value = "system_admin") SYSTEM_ADMIN("system_admin")
    }

    /**
     * GET api/v1/users
     * Получить список пользователей
     * Роли: system_admin. Требования: FR-3, FR-4, FR-6, FR-11, FR-12, FR-13.
     * Responses:
     *  - 200: Успешно
     *  - 400: Ошибка валидации запроса
     *  - 401: Нет или истёк токен доступа
     *  - 403: Недостаточно прав для операции
     *  - 500: Внутренняя ошибка
     *
     * @param idempotencyKey ключ идемпотентности; повтор с тем же ключом возвращает первый результат
     * @param role  (optional)
     * @param isActive  (optional)
     * @param page  (optional)
     * @param pageSize  (optional)
     * @param page2  (optional, default to 1)
     * @param pageSize2  (optional, default to 50)
     * @return [ListUsers200Response]
     */
    @GET("api/v1/users")
    suspend fun listUsers(@Header("Idempotency-Key") idempotencyKey: java.util.UUID, @Query("role") role: RoleListUsers? = null, @Query("isActive") isActive: kotlin.Boolean? = null, @Query("page") page: kotlin.Int? = null, @Query("pageSize") pageSize: kotlin.Int? = null, @Query("page") page2: kotlin.Int? = 1, @Query("pageSize") pageSize2: kotlin.Int? = 50): Response<ListUsers200Response>

}
