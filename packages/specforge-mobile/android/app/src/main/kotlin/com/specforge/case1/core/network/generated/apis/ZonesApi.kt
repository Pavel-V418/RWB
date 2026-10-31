package com.specforge.case1.core.network.generated.apis

import com.specforge.case1.core.network.generated.infrastructure.CollectionFormats.*
import retrofit2.http.*
import retrofit2.Response
import okhttp3.RequestBody
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

import com.specforge.case1.core.network.generated.models.PaymentLink
import com.specforge.case1.core.network.generated.models.Problem
import com.specforge.case1.core.network.generated.models.ValidationProblem
import com.specforge.case1.core.network.generated.models.ZoneDetails
import com.specforge.case1.core.network.generated.models.ZoneFeatureCollection
import com.specforge.case1.core.network.generated.models.ZoneLookupResult

interface ZonesApi {
    /**
     * GET api/v1/zones/{zoneId}
     * Карточка зоны с тарифами и геометрией
     * Полная информация о зоне: геометрия, тарифные периоды по типам ТС, правила оплаты.
     * Responses:
     *  - 200: Зона
     *  - 401: Нет или неверный токен
     *  - 404: Не найдено
     *  - 429: Превышен лимит запросов
     *  - 500: Внутренняя ошибка сервиса; детали только в логах по traceId
     *
     * @param zoneId 
     * @return [ZoneDetails]
     */
    @GET("api/v1/zones/{zoneId}")
    suspend fun getZone(@Path("zoneId") zoneId: java.util.UUID): Response<ZoneDetails>

    /**
     * GET api/v1/zones/{zoneId}/payment-link
     * Ссылка на официальный сервис оплаты для зоны
     * Формирует ссылку на официальный сервис оплаты парковки с номером зоны. Собственного платёжного механизма нет.
     * Responses:
     *  - 200: Ссылка (deep link приложения и web-URL)
     *  - 401: Нет или неверный токен
     *  - 404: Не найдено
     *  - 429: Превышен лимит запросов
     *  - 500: Внутренняя ошибка сервиса; детали только в логах по traceId
     *
     * @param zoneId 
     * @param vehicleId 
     * @return [PaymentLink]
     */
    @GET("api/v1/zones/{zoneId}/payment-link")
    suspend fun getZonePaymentLink(@Path("zoneId") zoneId: java.util.UUID, @Query("vehicleId") vehicleId: java.util.UUID): Response<PaymentLink>

    /**
     * GET api/v1/zones
     * Зоны в прямоугольнике карты (GeoJSON)
     * Возвращает FeatureCollection для отрисовки на карте. Параметр &#x60;bbox&#x60; обязателен, площадь ограничена.
     * Responses:
     *  - 200: Зоны
     *  - 400: Ошибка валидации
     *  - 401: Нет или неверный токен
     *  - 429: Превышен лимит запросов
     *  - 500: Внутренняя ошибка сервиса; детали только в логах по traceId
     *
     * @param bbox minLon,minLat,maxLon,maxLat
     * @param city  (optional)
     * @param limit  (optional, default to 500)
     * @return [ZoneFeatureCollection]
     */
    @GET("api/v1/zones")
    suspend fun listZones(@Query("bbox") bbox: kotlin.String, @Query("city") city: kotlin.String? = null, @Query("limit") limit: kotlin.Int? = 500): Response<ZoneFeatureCollection>

    /**
     * GET api/v1/zones/by-location
     * Определить зону по координатам
     * Ищет ближайшую активную зону, расстояние до геометрии которой не больше её detectionRadiusM.
     * Responses:
     *  - 200: Результат поиска; `zone` = null, если точка вне платных зон
     *  - 400: Ошибка валидации
     *  - 401: Нет или неверный токен
     *  - 429: Превышен лимит запросов
     *  - 500: Внутренняя ошибка сервиса; детали только в логах по traceId
     *
     * @param lat 
     * @param lon 
     * @return [ZoneLookupResult]
     */
    @GET("api/v1/zones/by-location")
    suspend fun lookupZone(@Query("lat") lat: kotlin.Double, @Query("lon") lon: kotlin.Double): Response<ZoneLookupResult>

}
