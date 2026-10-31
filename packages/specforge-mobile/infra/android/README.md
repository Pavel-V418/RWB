# Образ песочницы Android: `specforge/android:jdk21-api36`

> **В монорепозитории** образ собирается из корня пакета: `docker build -f packages/specforge-mobile/infra/android/Dockerfile -t specforge/android:jdk21-api36 packages/specforge-mobile`, проверка — `bash packages/specforge-mobile/infra/android/smoke.sh`. Пути `infra/images/android/…` ниже — из отдельного репозитория участника 5. Агент `android_coder` берёт образ из `SPECFORGE_ANDROID_IMAGE` (по умолчанию этот тег).

Образ для `sandbox_image` агента `android_coder` (платформа участника 1).
Содержит JDK 21, Android SDK (platform 36, build-tools 35/36), git, python3 и прогретые кэши
(Gradle 8.14.3, зависимости каркаса, android-all для Robolectric). Кэши лежат в `/opt/gradle-home` и
`/opt/home`, вне `/work`, поэтому том рабочей папки их не перекрывает. Генератор скриншот-тестов —
`/opt/specforge/screens.py`.

Команды шлюза внутри образа:
```
python3 /opt/specforge/screens.py . --spec <ux-спека> generate
python3 /opt/specforge/screens.py . --spec <ux-спека> check
./gradlew --no-daemon --offline assembleDebug testDebugUnitTest recordRoborazziDebug
python3 /opt/specforge/screens.py . --spec <ux-спека> verify
```

## Сборка на vm5 (Docker есть только там)
1. На ноутбуке, в терминале PyCharm, из папки `project rwb` скопировать архив на vm5. Пароль vm5 вводишь сам:
   ```
   scp -P 10005 build/android_image_ctx.tar.gz team5@85.143.113.230:~/
   ```
2. Зайти на vm5: `ssh -p 10005 team5@85.143.113.230`
3. На vm5:
   ```
   mkdir -p ~/android-image && cd ~/android-image && tar xzf ~/android_image_ctx.tar.gz
   docker build -f infra/images/android/Dockerfile -t specforge/android:jdk21-api36 . 2>&1 | tee build.log
   ```
   Первая сборка скачивает около 1,5 ГБ и прогревает кэш сборкой каркаса: 15–30 минут.
4. Проверка в условиях песочницы (без сети, лимит памяти, 512 процессов):
   ```
   bash infra/images/android/smoke.sh specforge/android:jdk21-api36 3g
   docker images specforge/android
   ```
   Должно быть `ГОТОВО` и `снимков: 7`.

## Проверено на vm5 (31.10)
`smoke.sh` в условиях песочницы: без сети (`--offline`), память 3 ГБ, `--pids-limit 512`, запуск не от root.
Результат: BUILD SUCCESSFUL за 83 с (23 задачи из кэша сборки), снимков 7. Размер образа — 1,88 ГБ (на диске 5,3 ГБ
вместе с распакованными слоями). Прогрев внутри `docker build` занимает около 9 минут.

## Риски
- На vm5 3,8 ГБ RAM. Сборка ограничена в `gradle.properties` (Gradle 1,5 ГБ, Kotlin 0,75 ГБ, тесты 1 ГБ,
  2 воркера) и проходит на ноутбуке. Если в контейнере 3 ГБ не хватит (OOM, `exit 137`), пришли конец `build.log`.
- `permission denied ... docker.sock`: пользователь team5 не в группе docker, это вопрос к куратору.
- Тег `eclipse-temurin:21-jdk-jammy` и `commandlinetools-linux-11076708` не проверены сборкой.
