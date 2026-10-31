using App.Application.Abstractions;
using App.Infrastructure.Observability;
using App.Infrastructure.Persistence;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace App.Infrastructure.Messaging;

/// <summary>
/// Фоновая публикация outbox: Kafka (гарантия at-least-once), затем SignalR (best effort).
/// Несколько экземпляров API безопасны: строки забираются через FOR UPDATE SKIP LOCKED.
/// После <see cref="KafkaOptions.MaxAttempts"/> неудач сообщение уходит в топик "&lt;topic&gt;.dlq".
/// </summary>
internal sealed partial class OutboxPublisher(
    IServiceScopeFactory scopeFactory,
    IMessageBus bus,
    OutboxSignal signal,
    TimeProvider clock,
    IOptions<KafkaOptions> options,
    AppMetrics metrics,
    ILogger<OutboxPublisher> logger) : BackgroundService
{
    private readonly KafkaOptions _options = options.Value;

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        while (!stoppingToken.IsCancellationRequested)
        {
            int processed;
            try
            {
                processed = await PublishBatchAsync(stoppingToken);
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
            {
                break;
            }
            catch (Exception ex) when (ex is DbUpdateException or Npgsql.NpgsqlException or InvalidOperationException)
            {
                LogBatchFailed(logger, ex);
                processed = 0;
            }

            if (processed < _options.BatchSize)
            {
                try
                {
                    await signal.WaitAsync(TimeSpan.FromMilliseconds(_options.PollIntervalMs), stoppingToken);
                }
                catch (OperationCanceledException)
                {
                    break;
                }
            }
        }
    }

    /// <summary>Публикует одну пачку. Возвращает число обработанных сообщений.</summary>
    internal async Task<int> PublishBatchAsync(CancellationToken cancellationToken)
    {
        await using var scope = scopeFactory.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();

        await using var tx = await db.Database.BeginTransactionAsync(cancellationToken);
        var batch = await db.OutboxMessages
            .FromSql($"""
                SELECT * FROM outbox_messages
                WHERE published_at IS NULL AND dead_lettered_at IS NULL
                ORDER BY created_at
                LIMIT {_options.BatchSize}
                FOR UPDATE SKIP LOCKED
                """)
            .ToListAsync(cancellationToken);

        var published = new List<OutboxMessage>(batch.Count);
        foreach (var message in batch)
        {
            try
            {
                await bus.PublishAsync(message.Topic, message.PartitionKey, message.Envelope, cancellationToken);
                message.PublishedAt = clock.GetUtcNow();
                published.Add(message);
                metrics.OutboxPublished(message.EventType);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                message.Attempts++;
                metrics.OutboxFailed(message.EventType);
                message.LastError = ex.Message.Length > 1000 ? ex.Message[..1000] : ex.Message;
                LogPublishFailed(logger, message.Id, message.Attempts, ex);
                if (message.Attempts >= _options.MaxAttempts)
                {
                    await TryDeadLetterAsync(message, cancellationToken);
                }
            }
        }

        await db.SaveChangesAsync(cancellationToken);
        await tx.CommitAsync(cancellationToken);

        if (published.Count > 0)
        {
            var notifier = scope.ServiceProvider.GetRequiredService<IRealtimeNotifier>();
            foreach (var message in published)
            {
                await NotifyRealtimeAsync(notifier, message, cancellationToken);
            }
        }

        return batch.Count;
    }

    private async Task TryDeadLetterAsync(OutboxMessage message, CancellationToken cancellationToken)
    {
        try
        {
            await bus.PublishAsync(message.Topic + ".dlq", message.PartitionKey, message.Envelope, cancellationToken);
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            LogPublishFailed(logger, message.Id, message.Attempts, ex);
        }

        // Даже если DLQ недоступна, прекращаем бесконечные попытки; строка остаётся в БД для разбора
        message.DeadLetteredAt = clock.GetUtcNow();
        metrics.OutboxDeadLettered(message.EventType);
    }

    private async Task NotifyRealtimeAsync(IRealtimeNotifier notifier, OutboxMessage message, CancellationToken cancellationToken)
    {
        var envelope = EventEnvelope.FromJson(message.Envelope);
        if (envelope is null)
        {
            return;
        }

        try
        {
            await notifier.PublishAsync(envelope.Type, envelope, envelope.PartitionKey, cancellationToken);
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            LogRealtimeFailed(logger, message.Id, ex);
        }
    }

    [LoggerMessage(Level = LogLevel.Error, Message = "Outbox: ошибка обработки пачки")]
    private static partial void LogBatchFailed(ILogger logger, Exception ex);

    [LoggerMessage(Level = LogLevel.Warning, Message = "Outbox: не удалось опубликовать {EventId}, попытка {Attempt}")]
    private static partial void LogPublishFailed(ILogger logger, Guid eventId, int attempt, Exception ex);

    [LoggerMessage(Level = LogLevel.Warning, Message = "Outbox: не удалось отправить {EventId} в SignalR")]
    private static partial void LogRealtimeFailed(ILogger logger, Guid eventId, Exception ex);
}
