package com.specforge.case1.core.network.generated.apis

import com.specforge.case1.core.network.generated.infrastructure.CollectionFormats.*
import retrofit2.http.*
import retrofit2.Response
import okhttp3.RequestBody
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

import com.specforge.case1.core.network.generated.models.ImportEmployeesRequest
import com.specforge.case1.core.network.generated.models.Problem

interface EmployeeImportsApi {
    /**
     * POST api/v1/employee-imports
     * Загрузить справочник сотрудников из CSV-файла
     * Роли: system_admin. Требования: FR-11.
     * Responses:
     *  - 201: Успешно
     *  - 400: Ошибка валидации запроса
     *  - 401: Нет или истёк токен доступа
     *  - 403: Недостаточно прав для операции
     *  - 422: Нарушено бизнес-правило
     *  - 500: Внутренняя ошибка
     *
     * @param importEmployeesRequest 
     * @return [kotlinx.serialization.json.JsonObject]
     */
    @POST("api/v1/employee-imports")
    suspend fun importEmployees(@Body importEmployeesRequest: ImportEmployeesRequest): Response<kotlinx.serialization.json.JsonObject>

}
