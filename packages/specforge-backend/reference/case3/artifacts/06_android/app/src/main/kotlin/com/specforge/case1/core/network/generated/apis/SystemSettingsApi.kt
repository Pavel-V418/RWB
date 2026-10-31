package com.specforge.case1.core.network.generated.apis

import com.specforge.case1.core.network.generated.infrastructure.CollectionFormats.*
import retrofit2.http.*
import retrofit2.Response
import okhttp3.RequestBody
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

import com.specforge.case1.core.network.generated.models.Problem
import com.specforge.case1.core.network.generated.models.SystemSetting
import com.specforge.case1.core.network.generated.models.UpdateSystemSettingRequest

interface SystemSettingsApi {
    /**
     * GET api/v1/system-settings
     * Получить настройки системы
     * Роли: system_admin, warehouse_manager. Требования: FR-7.
     * Responses:
     *  - 200: Успешно
     *  - 401: Нет или истёк токен доступа
     *  - 403: Недостаточно прав для операции
     *  - 500: Внутренняя ошибка
     *
     * @param idempotencyKey ключ идемпотентности; повтор с тем же ключом возвращает первый результат
     * @return [kotlin.collections.List<SystemSetting>]
     */
    @GET("api/v1/system-settings")
    suspend fun getSystemSettings(@Header("Idempotency-Key") idempotencyKey: java.util.UUID): Response<kotlin.collections.List<SystemSetting>>

    /**
     * PATCH api/v1/system-settings/{key}
     * Обновить настройку системы
     * Роли: system_admin. Требования: FR-7.
     * Responses:
     *  - 200: Успешно
     *  - 400: Ошибка валидации запроса
     *  - 401: Нет или истёк токен доступа
     *  - 403: Недостаточно прав для операции
     *  - 404: Ресурс не найден
     *  - 422: Нарушено бизнес-правило
     *  - 500: Внутренняя ошибка
     *
     * @param key 
     * @param idempotencyKey ключ идемпотентности; повтор с тем же ключом возвращает первый результат
     * @param updateSystemSettingRequest 
     * @return [SystemSetting]
     */
    @PATCH("api/v1/system-settings/{key}")
    suspend fun updateSystemSetting(@Path("key") key: kotlin.String, @Header("Idempotency-Key") idempotencyKey: java.util.UUID, @Body updateSystemSettingRequest: UpdateSystemSettingRequest): Response<SystemSetting>

}
