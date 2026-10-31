package com.specforge.case1.core.network.generated.apis

import com.specforge.case1.core.network.generated.infrastructure.CollectionFormats.*
import retrofit2.http.*
import retrofit2.Response
import okhttp3.RequestBody
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

import com.specforge.case1.core.network.generated.models.NotificationPage
import com.specforge.case1.core.network.generated.models.Problem
import com.specforge.case1.core.network.generated.models.UpdateNotificationRequest
import com.specforge.case1.core.network.generated.models.ValidationProblem

interface NotificationsApi {
    /**
     * GET api/v1/notifications
     * Уведомления водителя
     * Уведомления по автомобилю, новые сверху.
     * Responses:
     *  - 200: Страница уведомлений
     *  - 401: Нет или неверный токен
     *  - 403: Недостаточно прав
     *  - 429: Превышен лимит запросов
     *  - 500: Внутренняя ошибка сервиса; детали только в логах по traceId
     *
     * @param vehicleId 
     * @param unreadOnly  (optional, default to false)
     * @param page  (optional, default to 1)
     * @param pageSize  (optional, default to 50)
     * @return [NotificationPage]
     */
    @GET("api/v1/notifications")
    suspend fun listNotifications(@Query("vehicleId") vehicleId: java.util.UUID, @Query("unreadOnly") unreadOnly: kotlin.Boolean? = false, @Query("page") page: kotlin.Int? = 1, @Query("pageSize") pageSize: kotlin.Int? = 50): Response<NotificationPage>

    /**
     * PATCH api/v1/notifications/{notificationId}
     * Отметить уведомление прочитанным
     * Меняет признак прочтения. Повторный вызов с тем же значением ничего не меняет.
     * Responses:
     *  - 204: Сохранено
     *  - 400: Ошибка валидации
     *  - 401: Нет или неверный токен
     *  - 404: Не найдено
     *  - 429: Превышен лимит запросов
     *  - 500: Внутренняя ошибка сервиса; детали только в логах по traceId
     *
     * @param notificationId 
     * @param updateNotificationRequest 
     * @return [Unit]
     */
    @PATCH("api/v1/notifications/{notificationId}")
    suspend fun markNotificationRead(@Path("notificationId") notificationId: java.util.UUID, @Body updateNotificationRequest: UpdateNotificationRequest): Response<Unit>

}
