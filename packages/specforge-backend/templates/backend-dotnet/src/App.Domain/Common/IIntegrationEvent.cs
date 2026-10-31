namespace App.Domain.Common;

/// <summary>
/// Событие, которое уходит наружу: в Kafka (через outbox) и в SignalR.
/// Реализации — небольшие предметные record-ы, например <c>ZoneEntered</c>.
/// </summary>
public interface IIntegrationEvent
{
    /// <summary>Имя типа события в PascalCase, совпадает с AsyncAPI (например, "ZoneEntered").</summary>
    string EventType { get; }

    /// <summary>Версия схемы payload. Увеличивается при несовместимом изменении.</summary>
    int Version { get; }

    /// <summary>Kafka-топик, в который публикуется событие.</summary>
    string Topic { get; }

    /// <summary>Ключ партиционирования: события с одним ключом обрабатываются по порядку.</summary>
    string PartitionKey { get; }

    /// <summary>Момент, когда событие произошло в предметной области (UTC).</summary>
    DateTimeOffset OccurredAt { get; }
}
