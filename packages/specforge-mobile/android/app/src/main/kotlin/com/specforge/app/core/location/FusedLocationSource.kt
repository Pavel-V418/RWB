package com.specforge.app.core.location

import android.annotation.SuppressLint
import android.content.Context
import android.os.Looper
import com.google.android.gms.location.LocationCallback
import com.google.android.gms.location.LocationRequest
import com.google.android.gms.location.LocationResult
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow

/** Реальный GPS через Fused Location Provider. Разрешение запрашивает UI до старта трекинга. */
class FusedLocationSource(
    context: Context,
    private val intervalMs: Long = 5_000,
) : LocationSource {
    private val client = LocationServices.getFusedLocationProviderClient(context)

    @SuppressLint("MissingPermission")
    override fun fixes(): Flow<GeoFix> = callbackFlow {
        val request = LocationRequest.Builder(Priority.PRIORITY_HIGH_ACCURACY, intervalMs).build()
        val callback = object : LocationCallback() {
            override fun onLocationResult(result: LocationResult) {
                result.locations.forEach { l ->
                    trySend(
                        GeoFix(
                            lat = l.latitude,
                            lon = l.longitude,
                            recordedAtMillis = l.time,
                            speedKmh = if (l.hasSpeed()) l.speed * 3.6 else null,
                            headingDeg = if (l.hasBearing()) l.bearing.toDouble() else null,
                            accuracyM = if (l.hasAccuracy()) l.accuracy.toDouble() else null,
                        )
                    )
                }
            }
        }
        client.requestLocationUpdates(request, callback, Looper.getMainLooper())
        awaitClose { client.removeLocationUpdates(callback) }
    }
}
