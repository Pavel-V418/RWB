package com.specforge.app.feature.session

import com.specforge.app.core.auth.TokenStore
import com.specforge.app.core.location.GeoFix
import com.specforge.app.core.network.ApiResult
import com.specforge.app.core.network.apiCall
import com.specforge.app.core.tracking.Alert
import com.specforge.app.core.tracking.PositionSink
import com.specforge.app.feature.wiring.GeneratedApi
import com.specforge.case1.core.network.generated.models.ExtendSessionRequest
import com.specforge.case1.core.network.generated.models.LoginRequest
import com.specforge.case1.core.network.generated.models.ParkingSession
import com.specforge.case1.core.network.generated.models.PositionPoint
import com.specforge.case1.core.network.generated.models.ReportPositionsRequest
import com.specforge.case1.core.network.generated.models.StartSessionRequest
import com.specforge.case1.core.network.generated.models.ZoneSummary
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.util.UUID

class ApiSessionRepository(
    private val api: GeneratedApi,
    private val tokenStore: TokenStore,
    private val vehicleId: UUID,
    private val mockSource: Boolean,
) : SessionRepository, PositionSink {

    private val _live = MutableStateFlow<LiveStatus?>(null)
    override val live: StateFlow<LiveStatus?> = _live.asStateFlow()

    override suspend fun login(login: String, password: String): ApiResult<Unit> =
        when (val r = apiCall { api.auth.login(LoginRequest(login = login, password = password)) }) {
            is ApiResult.Ok -> { tokenStore.set(r.value.accessToken); ApiResult.Ok(Unit) }
            is ApiResult.Fail -> if (r.code == 401) ApiResult.Fail(401, "Неверный логин или пароль") else r
        }

    override suspend fun refresh(): ApiResult<LiveStatus> =
        when (val r = apiCall { api.vehicles.getVehicle(vehicleId) }) {
            is ApiResult.Ok -> {
                val v = r.value
                val status = LiveStatus(v.plateNumber, v.currentZone?.toInfo(), v.activeSession?.toActive())
                _live.value = status
                ApiResult.Ok(status)
            }
            is ApiResult.Fail -> r
        }

    override suspend fun start(zoneId: String, minutes: Int, idempotencyKey: String): ApiResult<ActiveSession> =
        sessionCall {
            api.sessions.startSession(
                idempotencyKey,
                StartSessionRequest(vehicleId = vehicleId, zoneId = UUID.fromString(zoneId), durationMinutes = minutes),
            )
        }

    override suspend fun extend(sessionId: String, minutes: Int, idempotencyKey: String): ApiResult<ActiveSession> =
        sessionCall {
            api.sessions.extendSession(UUID.fromString(sessionId), idempotencyKey, ExtendSessionRequest(additionalMinutes = minutes))
        }

    private suspend fun sessionCall(block: suspend () -> retrofit2.Response<ParkingSession>): ApiResult<ActiveSession> =
        when (val r = apiCall(block)) {
            is ApiResult.Ok -> {
                val active = r.value.toActive()
                _live.value = _live.value?.copy(session = active)
                ApiResult.Ok(active)
            }
            is ApiResult.Fail -> r
        }

    /** PositionSink: отправка пачки координат; ответ сервера сразу обновляет экран. */
    override suspend fun send(batch: List<GeoFix>): List<Alert> {
        val request = ReportPositionsRequest(
            points = batch.map {
                PositionPoint(
                    lat = it.lat,
                    lon = it.lon,
                    recordedAt = OffsetDateTime.ofInstant(Instant.ofEpochMilli(it.recordedAtMillis), ZoneOffset.UTC),
                    accuracyM = it.accuracyM,
                    speedKmh = it.speedKmh,
                    headingDeg = it.headingDeg,
                )
            },
            source = if (mockSource) ReportPositionsRequest.Source.MOCK else ReportPositionsRequest.Source.GPS,
        )
        val response = api.tracking.reportPositions(vehicleId, request)
        val body = response.body()
        if (!response.isSuccessful || body == null) error("positions: HTTP ${response.code()}")

        _live.value = LiveStatus(
            vehicleTitle = _live.value?.vehicleTitle,
            zone = body.currentZone?.toInfo(),
            session = body.activeSession?.toActive(),
        )
        return body.notifications.orEmpty().map {
            Alert(id = it.id.toString(), title = it.title, body = it.body, important = it.severity.value == "warning")
        }
    }

    private fun ZoneSummary.toInfo() = ZoneInfo(
        id = id.toString(),
        title = displayName,
        priceText = priceDescription,
        minMinutes = minTimeMinutes,
        stepMinutes = timeStepMinutes,
    )

    private fun ParkingSession.toActive() = ActiveSession(
        id = id.toString(),
        zoneTitle = zone.displayName,
        paidUntilText = paidUntil.atZoneSameInstant(ZoneId.systemDefault()).format(TIME),
        remainingSeconds = remainingSeconds.toLong(),
    )

    private companion object {
        val TIME: DateTimeFormatter = DateTimeFormatter.ofPattern("HH:mm")
    }
}
