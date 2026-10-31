package com.specforge.app

/**
 * Привязка теста к требованию для матрицы трассируемости (участник 2).
 * Формат: "FR-1" или "AC-FR-1-01". Матрица ищет тег и в имени теста — дублируйте его туда.
 */
@Target(AnnotationTarget.FUNCTION, AnnotationTarget.CLASS)
@Retention(AnnotationRetention.RUNTIME)
annotation class Req(vararg val ids: String)
