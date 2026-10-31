# Контракт Android-агента с каркасом

Этот файл — часть системного промпта Android-агента.

## Куда можно писать
- `app/src/main/kotlin/com/specforge/app/feature/**` — экраны, ViewModel, репозитории, навигация.
- `app/src/test/kotlin/com/specforge/app/feature/**` — unit- и скриншот-тесты своих экранов.
- `app/src/main/res/values/strings_<screenId>.xml` — тексты экрана (имена строк с префиксом `<screenId>_`);
  `strings.xml` — общие тексты.

## Куда нельзя
Всё из `frozen.sha256` (Gradle, манифест, `core/**`, `App.kt`, `MainActivity.kt`) и
`.../core/network/generated/**` (клиент API генерирует участник 3). Шлюз вернёт ошибку категории `contract`.
Новые зависимости добавлять нельзя.

## Обязательные точки входа
- `class FeatureModule(container: AppContainer)` с полем `positionSink: PositionSink`.
- `@Composable fun AppRoot(container: AppContainer)` — корень навигации.

## Правила экранов
1. id маршрута = `screenId` из `artifacts/03_ux/screen-spec.android.json`.
2. Экран делится на `XxxRoute(vm)` (собирает состояние) и stateless `XxxScreen(state, callbacks)`.
3. Содержимое оборачивается в `StateContainer(screenId, state, onRetry)`: он сам рисует
   loading / empty / error / forbidden и ставит testTag `<screenId>`, `<screenId>-state-<state>`.
4. Интерактивные элементы получают testTag `<screenId>-<элемент>`.
5. ViewModel принимает зависимости через конструктор (репозиторий-интерфейс), без Android-классов.
6. Ошибки сети — через `apiCall { }` → `ApiResult`, пользователю — понятный текст.
7. Действия, меняющие данные (`Idempotency-Key`), получают новый UUID на каждое нажатие; повторное нажатие
   во время запроса игнорируется.
8. На каждый экран: unit-тест ViewModel (успех, ошибка, пустое состояние) и в основном коде
   `@Composable fun <Pascal>ScreenSample(state: String)` — экран в каждом состоянии из UX-спеки на примерных
   данных (`<Pascal>Samples.kt` рядом с экраном). Скриншот-тесты агент НЕ пишет: конвейер генерирует
   `app/src/test/.../generated/ScreenshotTest.kt` из спеки (`tools/android_gate/screens.py`) →
   `screenshots/<screenId>--<state>--mobile.png`. Нет снимка — значит, падает экран или его Sample.
9. Теги требований: `@Req("FR-1")` и тот же тег в имени теста.

## Образец
`feature/session/*` — эталонная реализация кейса №1: смотри, как устроены Route/Screen/ViewModel/тесты.
