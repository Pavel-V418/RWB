# ADR-005. CI продукта: shell-runner на командной VM, стенд на docker compose, MR на каждый этап

## Контекст и постановка проблемы
Продукт нужно собирать, тестировать, сканировать и разворачивать автоматически, а результат каждого этапа конвейера
SpecForge показывать отдельным merge request. Ресурсы: одна VM (4 CPU, 3,8 ГБ RAM), заблокирован Maven Central,
нет registry и Kubernetes.

## Статус
ПРИНЯТО

## Альтернативы

### Вариант 1. Docker executor + registry + деплой в Kubernetes
- Плюсы
  - изоляция задач, промышленная схема.
- Минусы
  - нет registry и кластера; Testcontainers внутри docker executor требует docker-in-docker; не помещается в 3,8 ГБ.

### Вариант 2. Shell executor на той же VM, образы в локальном Docker, стенд — docker compose
- Плюсы
  - образ, собранный в package, сразу доступен Trivy и деплою без registry;
  - Testcontainers работает через Docker раннера; стенд поднимается `compose up --wait` с healthcheck;
  - укладывается в память: стенд ~0,5 ГБ, лимиты на каждый сервис.
- Минусы
  - задачи не изолированы друг от друга (`concurrent = 1`);
  - масштабирование — только переносом на другую схему.

## Принятое решение
Вариант 2. Стадии `build → test → package → security → deploy → e2e`; задачи QA подключаются `include` из `ci/*.gitlab-ci.yml`;
деплой только из основной ветки (`08_infra/deploy.sh`, секреты вне репозитория); e2e — `07_tests/e2e/*.py --base $STAND_URL`.
Каждый этап конвейера SpecForge уходит отдельной веткой `stage/<этап>` с push-опциями GitLab (`generators/stage-mr.sh`):
MR создаётся автоматически, в описании — результат шлюза этапа.

## Последствия принятого решения
- Раннер ставится без sudo (`scripts/vm-runner.sh`), бинарник из официального образа.
- Пайплайн проверен локально `gitlab-ci-local`; переход на docker executor потребует registry и сервиса dind.

## Ответственные
Участник 3 (backend + DevOps), участник 5 (задачи QA и безопасности).
