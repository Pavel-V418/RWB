# Запуск

!!! warning "Ожидается: инструкция по запуску"
    Раздел заполнится автоматически, когда участник 3 (DevOps) положит результат в `artifacts/08_infra/README-run.md`, и этап документации будет перезапущен.

## Общий порядок

Требуется Docker 24+ и Docker Compose v2. Из корня репозитория:

```bash
cp .env.example .env          # секреты и пароли задаются здесь, в репозитории их нет
docker compose up -d --build
docker compose ps              # все сервисы в состоянии healthy
```

## Компоненты

| Компонент | Технология |
| --- | --- |
| Mobile App | Kotlin |
| Web UI | Angular |
| Backend API | ASP.NET Core |
| Worker | ASP.NET Core Worker |
| Database | PostgreSQL |
| Kafka Broker | Kafka |
| TMЦ Mock Consumer | Mock |
