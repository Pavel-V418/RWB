package com.specforge.app.core.tracking

import com.specforge.app.core.location.GeoFix

/** Уведомление от сервера, которое ядро показывает пользователю. */
data class Alert(val id: String, val title: String, val body: String, val important: Boolean)

/**
 * Куда отправлять пачки координат. Реализует feature-слой поверх сгенерированного клиента API.
 * Возвращает уведомления из ответа сервера; при ошибке — исключение (пачка вернётся в буфер).
 */
interface PositionSink {
    suspend fun send(batch: List<GeoFix>): List<Alert>
}

/** Пачки по количеству или по времени — что наступит раньше. Чистая логика, покрыта тестом. */
class Batcher(
    private val maxSize: Int = 5,
    private val maxDelayMs: Long = 3_000,
    private val maxBuffer: Int = 500,
) {
    private val buffer = ArrayDeque<GeoFix>()
    private var lastFlushAt: Long? = null   // null — окно ещё не открыто (0 — валидное время)

    fun add(fix: GeoFix, nowMs: Long): List<GeoFix>? {
        if (buffer.size >= maxBuffer) buffer.removeFirst()   // офлайн: старые точки выбрасываем
        buffer.addLast(fix)
        val since = lastFlushAt ?: nowMs.also { lastFlushAt = it }
        return if (buffer.size >= maxSize || nowMs - since >= maxDelayMs) drain(nowMs) else null
    }

    /** Пачка не ушла — вернуть точки в начало буфера. */
    fun putBack(batch: List<GeoFix>) {
        batch.asReversed().forEach { if (buffer.size < maxBuffer) buffer.addFirst(it) }
    }

    private fun drain(nowMs: Long): List<GeoFix> {
        lastFlushAt = nowMs
        return buffer.toList().also { buffer.clear() }
    }

    val pending: Int get() = buffer.size
}
