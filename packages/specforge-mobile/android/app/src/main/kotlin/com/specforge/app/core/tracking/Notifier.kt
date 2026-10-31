package com.specforge.app.core.tracking

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.PackageManager
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat

class Notifier(private val context: Context) {

    fun createChannels() {
        val nm = context.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CH_TRACKING, "Отслеживание", NotificationManager.IMPORTANCE_LOW))
        nm.createNotificationChannel(NotificationChannel(CH_ALERTS, "Важные события", NotificationManager.IMPORTANCE_HIGH))
    }

    fun trackingNotification(): Notification =
        NotificationCompat.Builder(context, CH_TRACKING)
            .setSmallIcon(android.R.drawable.ic_menu_mylocation)
            .setContentTitle("Отслеживание включено")
            .setContentText("Местоположение передаётся на сервер")
            .setOngoing(true)
            .build()

    fun alert(alert: Alert) {
        if (!canPost()) return
        val n = NotificationCompat.Builder(context, CH_ALERTS)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(alert.title)
            .setContentText(alert.body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(alert.body))
            .setPriority(if (alert.important) NotificationCompat.PRIORITY_HIGH else NotificationCompat.PRIORITY_DEFAULT)
            .setAutoCancel(true)
            .build()
        try {
            NotificationManagerCompat.from(context).notify(alert.id.hashCode(), n)
        } catch (_: SecurityException) {
            // разрешение отозвали между проверкой и показом
        }
    }

    private fun canPost(): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED ||
            android.os.Build.VERSION.SDK_INT < 33

    companion object {
        const val TRACKING_ID = 1001
        private const val CH_TRACKING = "tracking"
        private const val CH_ALERTS = "alerts"
    }
}
