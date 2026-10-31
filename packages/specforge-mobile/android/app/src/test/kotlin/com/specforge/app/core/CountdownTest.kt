package com.specforge.app.core

import com.specforge.app.core.time.countdown
import com.specforge.app.core.time.formatDuration
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test

class CountdownTest {
    @Test
    fun `при TIME_SCALE=10 за секунду уходит 10 секунд`() = runTest {
        val values = countdown(remainingSeconds = 25, timeScale = 10).toList()
        assertEquals(listOf(25L, 15L, 5L, 0L), values)
    }

    @Test
    fun `без ускорения считает по секунде`() = runTest {
        assertEquals(listOf(3L, 2L, 1L, 0L), countdown(3, 1).toList())
    }

    @Test
    fun `форматирование`() {
        assertEquals("02:05", formatDuration(125))
        assertEquals("1:02:05", formatDuration(3725))
        assertEquals("00:00", formatDuration(-5))
    }
}
