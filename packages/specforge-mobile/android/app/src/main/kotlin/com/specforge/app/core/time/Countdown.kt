package com.specforge.app.core.time

import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow

/**
 * Обратный отсчёт в «сценарных» секундах. За одну реальную секунду проходит timeScale секунд
 * (как у backend с TIME_SCALE). Значение с сервера (remainingSeconds) периодически сверяется заново.
 */
fun countdown(remainingSeconds: Long, timeScale: Int, tickMs: Long = 1_000): Flow<Long> = flow {
    val step = (tickMs / 1000.0 * timeScale).toLong().coerceAtLeast(1)
    var left = remainingSeconds.coerceAtLeast(0)
    while (left > 0) {
        emit(left)
        delay(tickMs)
        left -= step
    }
    emit(0)
}

/** 3725 -> "1:02:05", 125 -> "02:05". */
fun formatDuration(totalSeconds: Long): String {
    val s = totalSeconds.coerceAtLeast(0)
    val h = s / 3600
    val m = (s % 3600) / 60
    val sec = s % 60
    return if (h > 0) "%d:%02d:%02d".format(h, m, sec) else "%02d:%02d".format(m, sec)
}
