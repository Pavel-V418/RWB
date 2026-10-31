package com.specforge.app.core

import com.specforge.app.core.location.GeoFix
import com.specforge.app.core.tracking.Batcher
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class BatcherTest {
    private fun fix(i: Int) = GeoFix(59.0 + i * 1e-4, 30.0, i.toLong())

    @Test
    fun `отдаёт пачку при наборе maxSize`() {
        val b = Batcher(maxSize = 3, maxDelayMs = 10_000)
        assertNull(b.add(fix(1), 0))
        assertNull(b.add(fix(2), 100))
        assertEquals(3, b.add(fix(3), 200)?.size)
        assertEquals(0, b.pending)
    }

    @Test
    fun `отдаёт пачку по таймауту`() {
        val b = Batcher(maxSize = 100, maxDelayMs = 1_000)
        assertNull(b.add(fix(1), 0))
        assertEquals(2, b.add(fix(2), 1_500)?.size)
    }

    @Test
    fun `неотправленная пачка возвращается в начало и уходит со следующей`() {
        val b = Batcher(maxSize = 2, maxDelayMs = 10_000)
        b.add(fix(1), 0)
        val batch = b.add(fix(2), 1)!!
        b.putBack(batch)
        val next = b.add(fix(3), 2)!!
        assertEquals(listOf(1L, 2L, 3L), next.map { it.recordedAtMillis })
    }

    @Test
    fun `буфер ограничен и выбрасывает старые точки`() {
        val b = Batcher(maxSize = 1000, maxDelayMs = Long.MAX_VALUE, maxBuffer = 5)
        repeat(8) { b.add(fix(it), it.toLong() + 1) }
        assertEquals(5, b.pending)
    }
}
