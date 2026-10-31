package com.specforge.case1.core.network.generated.apis

import com.specforge.case1.core.network.generated.infrastructure.CollectionFormats.*
import retrofit2.http.*
import retrofit2.Response
import okhttp3.RequestBody
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

import com.specforge.case1.core.network.generated.models.CreateVehicleRequest
import com.specforge.case1.core.network.generated.models.Problem
import com.specforge.case1.core.network.generated.models.ValidationProblem
import com.specforge.case1.core.network.generated.models.Vehicle
import com.specforge.case1.core.network.generated.models.VehiclePage
import com.specforge.case1.core.network.generated.models.VehicleStatus

interface VehiclesApi {
    /**
     * POST api/v1/vehicles
     * Добавить автомобиль (admin)
     * Регистрирует служебный автомобиль. Госномер уникален.
     * Responses:
     *  - 201: Создан
     *  - 400: Ошибка валидации
     *  - 401: Нет или неверный токен
     *  - 403: Недостаточно прав
     *  - 409: Конфликт состояния (например, уже есть активная сессия)
     *  - 429: Превышен лимит запросов
     *  - 500: Внутренняя ошибка сервиса; детали только в логах по traceId
     *
     * @param createVehicleRequest 
     * @return [Vehicle]
     */
    @POST("api/v1/vehicles")
    suspend fun createVehicle(@Body createVehicleRequest: CreateVehicleRequest): Response<Vehicle>

    /**
     * GET api/v1/vehicles/{vehicleId}
     * Автомобиль и его текущее состояние (зона, активная сессия, последняя точка)
     * Автомобиль вместе с текущей зоной, последней точкой и активной сессией.
     * Responses:
     *  - 200: Автомобиль
     *  - 401: Нет или неверный токен
     *  - 403: Недостаточно прав
     *  - 404: Не найдено
     *  - 429: Превышен лимит запросов
     *  - 500: Внутренняя ошибка сервиса; детали только в логах по traceId
     *
     * @param vehicleId 
     * @return [VehicleStatus]
     */
    @GET("api/v1/vehicles/{vehicleId}")
    suspend fun getVehicle(@Path("vehicleId") vehicleId: java.util.UUID): Response<VehicleStatus>

    /**
     * GET api/v1/vehicles
     * Автомобили (admin — все, driver — закреплённые за ним)
     * Администратор видит все автомобили, водитель — только закреплённые за ним.
     * Responses:
     *  - 200: Список
     *  - 401: Нет или неверный токен
     *  - 429: Превышен лимит запросов
     *  - 500: Внутренняя ошибка сервиса; детали только в логах по traceId
     *
     * @param page  (optional, default to 1)
     * @param pageSize  (optional, default to 50)
     * @return [VehiclePage]
     */
    @GET("api/v1/vehicles")
    suspend fun listVehicles(@Query("page") page: kotlin.Int? = 1, @Query("pageSize") pageSize: kotlin.Int? = 50): Response<VehiclePage>

}
