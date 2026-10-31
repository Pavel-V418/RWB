# Этап 3 — каркас Android и шлюз сборки (участник 5)

```
android/                         проект Android (открывать в Android Studio именно эту папку)
  app/src/main/kotlin/com/specforge/
    app/core/        ядро — универсальное, заморожено (frozen.sha256), доменных слов нет
    app/feature/     экраны — эталон кейса №1; для другого ТЗ их пишет Android-агент
    case1/core/network/generated/   клиент API от участника 3 (не править руками)
  app/src/main/assets/mock_route/points.json   маршрут из этапа 2
  app/src/test/...   unit-тесты + скриншоты Roborazzi
  gradle.properties  параметры кейса (appId, адрес API, mock-маршрут, TIME_SCALE) и лимиты памяти
  AGENT_CONTRACT.md  правила для Android-агента
  frozen.sha256      хэши файлов, которые агенту менять нельзя
tools/android_gate/
  android_gate.py    шлюз: сборка → тесты → скриншоты → gate_report.json (формат участника 1)
  test_android_gate.py
```

## Что в каркасе
- Kotlin 2.2.21, Compose + Material 3, Navigation, Retrofit 3 + kotlinx.serialization, minSdk 26.
- Ручной DI (`AppContainer`) вместо Hilt — сборке не нужен kapt/ksp: быстрее и меньше памяти.
- Геолокация: Fused Location или mock-маршрут (`specforge.mockRoute=true`), темп = `specforge.timeScale`.
- Foreground-сервис трекинга: пачки точек → `POST /positions`, уведомления сервера → системные уведомления.
- Таймер сессии с ускорением TIME_SCALE.
- Экраны `login` и `session`; все состояния ready/loading/empty/error/forbidden, testTag как в вебе.

## Первая сборка в Android Studio
1. File → Open → папка `android` (не корень архива).
2. Дождаться Gradle Sync (первый раз долго: качает зависимости).
   - Если предложит обновить AGP — можно согласиться.
   - Если попросит JDK 17 — Settings → Build Tools → Gradle → Gradle JDK → выбрать встроенный (jbr-17/21).
3. Терминал Android Studio (внизу), в папке `android`:
   ```
   .\gradlew.bat assembleDebug recordRoborazziDebug
   ```
   Появятся `app/build/outputs/apk/debug/app-debug.apk` и PNG в `screenshots/`.
4. Или сразу шлюзом (из папки, где лежат `android/` и `tools/`):
   ```
   python tools/android_gate/android_gate.py android
   ```
   Результат — `android/gate_report.json`.

## Запуск на эмуляторе
Device Manager → создать Pixel (API 35) → Run ▶. Логин `driver.demo`, пароль — у участника 3.
По умолчанию приложение ходит на Prism `http://10.0.2.2:4010/`. Для стенда поменять
`specforge.apiBaseUrl` в `gradle.properties` и добавить хост в `res/xml/network_security_debug.xml`,
если стенд по HTTP.

## Известные ограничения
- Сборка в песочнице, где писался каркас, невозможна (нет доступа к Maven/Google) — проверены синтаксис,
  чистая логика ядра и шлюз. Первая полноценная сборка — у тебя в Android Studio; ошибки пришли как есть.
- Зоны в mock-маршруте пока «прямоугольники-заглушки» — ждём `demo-zones.geojson` от участника 3.
- `DEMO_VEHICLE_ID` в `FeatureModule` — машина из сида; позже брать из профиля пользователя.
