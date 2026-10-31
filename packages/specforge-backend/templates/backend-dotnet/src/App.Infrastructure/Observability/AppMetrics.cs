using System.Diagnostics.Metrics;

namespace App.Infrastructure.Observability;

/// <summary>
/// Метрики приложения (System.Diagnostics.Metrics), экспортируются в Prometheus на /metrics.
/// Доменные метрики backend-агент добавляет сюда же или через свой Meter с префиксом <see cref="MeterName"/>.
/// </summary>
public sealed class AppMetrics
{
    public const string MeterName = "App";

    private readonly Counter<long> _outboxPublished;
    private readonly Counter<long> _outboxFailed;
    private readonly Counter<long> _outboxDeadLettered;

    public AppMetrics(IMeterFactory meterFactory)
    {
        ArgumentNullException.ThrowIfNull(meterFactory);
        var meter = meterFactory.Create(MeterName);
        _outboxPublished = meter.CreateCounter<long>("app_outbox_published", "{event}", "События, опубликованные в Kafka");
        _outboxFailed = meter.CreateCounter<long>("app_outbox_publish_failures", "{attempt}", "Неудачные попытки публикации");
        _outboxDeadLettered = meter.CreateCounter<long>("app_outbox_dead_lettered", "{event}", "События, отправленные в DLQ");
    }

    public void OutboxPublished(string eventType) => _outboxPublished.Add(1, new KeyValuePair<string, object?>("event_type", eventType));

    public void OutboxFailed(string eventType) => _outboxFailed.Add(1, new KeyValuePair<string, object?>("event_type", eventType));

    public void OutboxDeadLettered(string eventType) => _outboxDeadLettered.Add(1, new KeyValuePair<string, object?>("event_type", eventType));
}
