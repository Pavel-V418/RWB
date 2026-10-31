using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;
using App.Domain.Common;

namespace App.Infrastructure.Messaging;

/// <summary>
/// Оболочка сообщения в Kafka и SignalR (схема описана в AsyncAPI).
/// Правило повторной обработки: потребитель дедуплицирует по <see cref="EventId"/>.
/// </summary>
public sealed record EventEnvelope(
    Guid EventId,
    string Type,
    int Version,
    DateTimeOffset OccurredAt,
    string Source,
    string PartitionKey,
    JsonObject Payload)
{
    public const string DefaultSource = "backend";

    /// <summary>Общие настройки JSON: camelCase, перечисления строками.</summary>
    public static JsonSerializerOptions JsonOptions { get; } = CreateJsonOptions();

    // Служебные свойства события не дублируются в payload
    private static readonly string[] ServiceProperties =
        ["eventType", "version", "topic", "partitionKey", "occurredAt"];

    public static EventEnvelope From(IIntegrationEvent integrationEvent, Guid eventId)
    {
        ArgumentNullException.ThrowIfNull(integrationEvent);
        var payload = JsonSerializer.SerializeToNode(integrationEvent, integrationEvent.GetType(), JsonOptions)!.AsObject();
        foreach (var name in ServiceProperties)
        {
            payload.Remove(name);
        }

        return new EventEnvelope(
            eventId,
            integrationEvent.EventType,
            integrationEvent.Version,
            integrationEvent.OccurredAt.ToUniversalTime(),
            DefaultSource,
            integrationEvent.PartitionKey,
            payload);
    }

    public string ToJson() => JsonSerializer.Serialize(this, JsonOptions);

    public static EventEnvelope? FromJson(string json) => JsonSerializer.Deserialize<EventEnvelope>(json, JsonOptions);

    private static JsonSerializerOptions CreateJsonOptions()
    {
        var options = new JsonSerializerOptions(JsonSerializerDefaults.Web);
        options.Converters.Add(new JsonStringEnumConverter());
        return options;
    }
}
