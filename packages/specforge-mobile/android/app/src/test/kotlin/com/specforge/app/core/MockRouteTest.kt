package com.specforge.app.core

import com.specforge.app.core.location.MockRouteLocationSource
import kotlinx.coroutines.flow.take
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.test.currentTime
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class MockRouteTest {
    private val asset = File("src/main/assets/mock_route/points.json")

    @Test
    fun `маршрут из assets читается`() {
        val points = MockRouteLocationSource.parse(asset.readText())
        assertTrue("точек слишком мало: ${points.size}", points.size > 100)
        assertTrue(points.all { it.lat in 59.0..61.0 && it.lon in 29.0..31.0 })
    }

    @Test
    fun `интервалы делятся на timeScale`() = runTest {
        val json = """[{"lat":59.9,"lon":30.3,"_tSec":0},{"lat":59.9,"lon":30.3,"_tSec":10},{"lat":59.9,"lon":30.3,"_tSec":20}]"""
        val source = MockRouteLocationSource({ json }, timeScale = 10, clock = { 42L })
        val fixes = source.fixes().take(3).toList()
        assertEquals(3, fixes.size)
        assertEquals(2_000L, currentTime)          // 20 сценарных секунд / 10
        assertEquals(42L, fixes.last().recordedAtMillis)
    }
}
