Каркас Android-проекта для прогрева кэша Gradle при сборке образа `specforge/android:21`.

Положите сюда проект с `gradlew` (тот же Gradle, AGP и Kotlin 2.2.21, что генерирует агент) —
`infra/images/build.sh android` выполнит `./gradlew assembleDebug testDebugUnitTest`, и
зависимости окажутся в `/cache/gradle`. Без проекта образ собирается без прогрева.
