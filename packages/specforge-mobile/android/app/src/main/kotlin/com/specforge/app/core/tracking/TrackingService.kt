package com.specforge.app.core.tracking

import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleService
import androidx.lifecycle.lifecycleScope
import com.specforge.app.App
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * Foreground-сервис трекинга: берёт точки из LocationSource, отправляет пачками в PositionSink,
 * показывает уведомления сервера. Работает и при свёрнутом приложении.
 */
class TrackingService : LifecycleService() {

    private var job: Job? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        val container = (application as App).container
        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION else 0
        ServiceCompat.startForeground(this, Notifier.TRACKING_ID, container.notifier.trackingNotification(), type)

        if (job == null) {
            job = lifecycleScope.launch {
                _state.value = TrackingState(running = true)
                val batcher = Batcher()
                val sink = container.feature.positionSink
                container.locationSource.fixes().collect { fix ->
                    _state.value = _state.value.copy(lastFix = fix)
                    val batch = batcher.add(fix, System.currentTimeMillis()) ?: return@collect
                    try {
                        sink.send(batch).forEach { container.notifier.alert(it) }
                        _state.value = _state.value.copy(sent = _state.value.sent + batch.size, error = null)
                    } catch (e: CancellationException) {
                        throw e
                    } catch (e: Exception) {
                        batcher.putBack(batch)
                        _state.value = _state.value.copy(error = e.message ?: "ошибка отправки")
                    }
                }
                _state.value = _state.value.copy(running = false)
            }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        job?.cancel()
        _state.value = _state.value.copy(running = false)
        super.onDestroy()
    }

    data class TrackingState(
        val running: Boolean = false,
        val lastFix: com.specforge.app.core.location.GeoFix? = null,
        val sent: Int = 0,
        val error: String? = null,
    )

    companion object {
        private const val ACTION_STOP = "stop"
        private val _state = MutableStateFlow(TrackingState())
        val state: StateFlow<TrackingState> = _state.asStateFlow()

        fun start(context: Context) =
            ContextCompat.startForegroundService(context, Intent(context, TrackingService::class.java))

        fun stop(context: Context) =
            context.startService(Intent(context, TrackingService::class.java).setAction(ACTION_STOP))
    }
}
