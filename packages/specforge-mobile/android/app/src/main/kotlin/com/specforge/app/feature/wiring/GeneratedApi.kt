package com.specforge.app.feature.wiring

import com.specforge.case1.core.network.generated.apis.AuthApi
import com.specforge.case1.core.network.generated.apis.SessionsApi
import com.specforge.case1.core.network.generated.apis.TrackingApi
import com.specforge.case1.core.network.generated.apis.VehiclesApi
import com.specforge.case1.core.network.generated.apis.ZonesApi
import com.specforge.case1.core.network.generated.infrastructure.ApiClient
import okhttp3.OkHttpClient

/**
 * Связка с клиентом, который генерирует участник 3 (папка generated/ — только чтение).
 * Пакет generated зависит от appId кейса, поэтому импорт живёт здесь, а не в core/.
 */
class GeneratedApi(baseUrl: String, okHttp: OkHttpClient) {
    private val client = ApiClient(baseUrl = baseUrl, okHttpClientBuilder = okHttp.newBuilder())

    val auth: AuthApi by lazy { client.createService(AuthApi::class.java) }
    val sessions: SessionsApi by lazy { client.createService(SessionsApi::class.java) }
    val tracking: TrackingApi by lazy { client.createService(TrackingApi::class.java) }
    val vehicles: VehiclesApi by lazy { client.createService(VehiclesApi::class.java) }
    val zones: ZonesApi by lazy { client.createService(ZonesApi::class.java) }
}
