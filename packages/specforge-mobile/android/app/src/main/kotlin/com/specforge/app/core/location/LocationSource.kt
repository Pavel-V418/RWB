package com.specforge.app.core.location

import kotlinx.coroutines.flow.Flow

/** Одна отметка местоположения, независимая от источника (GPS или mock-маршрут). */
data class GeoFix(
    val lat: Double,
    val lon: Double,
    val recordedAtMillis: Long,
    val speedKmh: Double? = null,
    val headingDeg: Double? = null,
    val accuracyM: Double? = null,
)

/** Источник геолокации. Реализация выбирается в AppContainer по specforge.mockRoute. */
interface LocationSource {
    fun fixes(): Flow<GeoFix>
}
