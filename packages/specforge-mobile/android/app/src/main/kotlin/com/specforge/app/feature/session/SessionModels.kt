package com.specforge.app.feature.session

import com.specforge.app.core.network.ApiResult
import kotlinx.coroutines.flow.StateFlow

/** Модели экрана — не зависят от сгенерированного клиента, поэтому их легко тестировать. */
data class ZoneInfo(
    val id: String,
    val title: String,
    val priceText: String?,
    val minMinutes: Int,
    val stepMinutes: Int,
)

data class ActiveSession(
    val id: String,
    val zoneTitle: String,
    val paidUntilText: String,
    val remainingSeconds: Long,
)

data class LiveStatus(
    val vehicleTitle: String?,
    val zone: ZoneInfo?,
    val session: ActiveSession?,
)

data class SessionScreenData(
    val status: LiveStatus,
    val remainingSeconds: Long?,
    val tracking: Boolean,
    val busy: Boolean = false,
    val message: String? = null,
)

/** Всё, что экрану нужно от backend. Реальная реализация — ApiSessionRepository. */
interface SessionRepository {
    /** Свежий статус: обновляется и запросами, и ответами на отправку координат. */
    val live: StateFlow<LiveStatus?>
    suspend fun login(login: String, password: String): ApiResult<Unit>
    suspend fun refresh(): ApiResult<LiveStatus>
    suspend fun start(zoneId: String, minutes: Int, idempotencyKey: String): ApiResult<ActiveSession>
    suspend fun extend(sessionId: String, minutes: Int, idempotencyKey: String): ApiResult<ActiveSession>
}
