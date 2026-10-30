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
| Mobile App | Kotlin 2.2, Jetpack Compose, Material 3 |
| Admin Web | Angular 20, UI Kit, MapLibre |
| Backend API | ASP.NET Core 10, C# 14, EF Core + NetTopologySuite, SignalR |
| Background Worker | .NET 10 BackgroundService |
| PostgreSQL + PostGIS | PostgreSQL 17.9, PostGIS |
| Kafka | Apache Kafka |
| Parkomatica Mock | ASP.NET Core minimal API |
