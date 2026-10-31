package com.specforge.app.feature

import com.specforge.app.core.network.ApiResult
import com.specforge.app.feature.session.ActiveSession
import com.specforge.app.feature.session.LiveStatus
import com.specforge.app.feature.session.SessionRepository
import com.specforge.app.feature.session.SessionSamples
import com.specforge.app.feature.session.ZoneInfo
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.flow.MutableStateFlow

class FakeSessionRepository : SessionRepository {
    override val live = MutableStateFlow<LiveStatus?>(null)

    var refreshResult: ApiResult<LiveStatus> = ApiResult.Ok(STATUS_IN_ZONE)
    var startResult: ApiResult<ActiveSession> = ApiResult.Ok(SESSION)
    var loginResult: ApiResult<Unit> = ApiResult.Ok(Unit)
    /** Если задан — start() ждёт его, чтобы проверить состояние «занято». */
    var startGate: CompletableDeferred<Unit>? = null
    val startCalls = mutableListOf<Triple<String, Int, String>>()

    override suspend fun login(login: String, password: String) = loginResult
    override suspend fun refresh() = refreshResult
    override suspend fun start(zoneId: String, minutes: Int, idempotencyKey: String): ApiResult<ActiveSession> {
        startCalls += Triple(zoneId, minutes, idempotencyKey)
        startGate?.await()
        return startResult
    }
    override suspend fun extend(sessionId: String, minutes: Int, idempotencyKey: String) = startResult

    companion object {
        // данные — из SessionSamples (main), чтобы снимки и тесты видели одно и то же
        val ZONE = SessionSamples.ZONE
        val SESSION = SessionSamples.SESSION
        val STATUS_IN_ZONE = SessionSamples.STATUS_IN_ZONE
        val STATUS_WITH_SESSION = SessionSamples.STATUS_WITH_SESSION
    }
}
