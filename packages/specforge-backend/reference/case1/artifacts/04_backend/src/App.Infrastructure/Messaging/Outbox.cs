using App.Application.Abstractions;
using App.Domain.Common;
using App.Infrastructure.Persistence;

namespace App.Infrastructure.Messaging;

/// <summary>Кладёт событие в outbox_messages; сохраняется вместе с остальными изменениями контекста.</summary>
internal sealed class Outbox(AppDbContext db, TimeProvider clock) : IOutbox
{
    public void Enqueue(IIntegrationEvent integrationEvent)
    {
        var id = Guid.CreateVersion7();
        var envelope = EventEnvelope.From(integrationEvent, id);
        db.OutboxMessages.Add(new OutboxMessage
        {
            Id = id,
            EventType = envelope.Type,
            EventVersion = (short)envelope.Version,
            Topic = integrationEvent.Topic,
            PartitionKey = envelope.PartitionKey,
            OccurredAt = envelope.OccurredAt,
            Envelope = envelope.ToJson(),
            CreatedAt = clock.GetUtcNow(),
        });
    }
}

/// <summary>Сигнал «в outbox появились новые события» для мгновенного пробуждения публикатора.</summary>
public sealed class OutboxSignal : IDisposable
{
    private readonly SemaphoreSlim _semaphore = new(0, 1);

    public void Notify()
    {
        if (_semaphore.CurrentCount == 0)
        {
            try
            {
                _semaphore.Release();
            }
            catch (SemaphoreFullException)
            {
                // Сигнал уже выставлен другим потоком
            }
        }
    }

    public Task<bool> WaitAsync(TimeSpan timeout, CancellationToken cancellationToken) =>
        _semaphore.WaitAsync(timeout, cancellationToken);

    public void Dispose() => _semaphore.Dispose();
}
