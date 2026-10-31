package com.specforge.case1.core.network.generated.apis

import com.specforge.case1.core.network.generated.infrastructure.CollectionFormats.*
import retrofit2.http.*
import retrofit2.Response
import okhttp3.RequestBody
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

import com.specforge.case1.core.network.generated.models.Employee
import com.specforge.case1.core.network.generated.models.ListEmployees200Response
import com.specforge.case1.core.network.generated.models.Problem

interface EmployeesApi {
    /**
     * GET api/v1/employees/pass/{passNumber}
     * Получить сотрудника по номеру пропуска
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
     * @param passNumber 
     * @param idempotencyKey ключ идемпотентности; повтор с тем же ключом возвращает первый результат
     * @return [Employee]
     */
    @GET("api/v1/employees/pass/{passNumber}")
    suspend fun getEmployeeByPassNumber(@Path("passNumber") passNumber: kotlin.String, @Header("Idempotency-Key") idempotencyKey: java.util.UUID): Response<Employee>

    /**
     * GET api/v1/employees
     * Получить список сотрудников
     * Роли: warehouse_manager, warehouse_keeper, system_admin. Требования: FR-11.
     * Responses:
     *  - 200: Успешно
     *  - 400: Ошибка валидации запроса
     *  - 401: Нет или истёк токен доступа
     *  - 403: Недостаточно прав для операции
     *  - 500: Внутренняя ошибка
     *
     * @param idempotencyKey ключ идемпотентности; повтор с тем же ключом возвращает первый результат
     * @param name  (optional)
     * @param passNumber  (optional)
     * @param page  (optional)
     * @param pageSize  (optional)
     * @param page2  (optional, default to 1)
     * @param pageSize2  (optional, default to 50)
     * @return [ListEmployees200Response]
     */
    @GET("api/v1/employees")
    suspend fun listEmployees(@Header("Idempotency-Key") idempotencyKey: java.util.UUID, @Query("name") name: kotlin.String? = null, @Query("passNumber") passNumber: kotlin.String? = null, @Query("page") page: kotlin.Int? = null, @Query("pageSize") pageSize: kotlin.Int? = null, @Query("page") page2: kotlin.Int? = 1, @Query("pageSize") pageSize2: kotlin.Int? = 50): Response<ListEmployees200Response>

}
