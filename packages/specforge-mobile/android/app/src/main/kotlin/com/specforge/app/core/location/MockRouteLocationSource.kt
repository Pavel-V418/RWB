package com.specforge.app.core.location

import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/**
 * Проигрывает points.json, созданный tools/route_player/geo_scenario.py.
 * Интервалы между точками делятся на timeScale (должен совпадать с TIME_SCALE backend).
 * Время отметки — текущее, а не из файла: backend считает таймеры от «сейчас».
 */
class MockRouteLocationSource(
    private val readJson: () -> String,
    private val timeScale: Int,
    private val clock: () -> Long = System::currentTimeMillis,
) : LocationSource {

    @Serializable
    data class MockPoint(
        val lat: Double,
        val lon: Double,
        val speedKmh: Double? = null,
        val headingDeg: Double? = null,
        val accuracyM: Double? = null,
        @SerialName("_tSec") val tSec: Double,
    )

    override fun fixes(): Flow<GeoFix> = flow {
        val points = parse(readJson())
        var prevT = points.firstOrNull()?.tSec ?: return@flow
        for (p in points) {
            val waitMs = ((p.tSec - prevT) * 1000 / timeScale).toLong()
            if (waitMs > 0) delay(waitMs)
            prevT = p.tSec
            emit(GeoFix(p.lat, p.lon, clock(), p.speedKmh, p.headingDeg, p.accuracyM))
        }
    }

    companion object {
        private val json = Json { ignoreUnknownKeys = true }
        fun parse(text: String): List<MockPoint> = json.decodeFromString(text)
    }
}
