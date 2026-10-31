using Confluent.Kafka;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace App.Infrastructure.Messaging;

public sealed class KafkaOptions
{
    public const string SectionName = "Kafka";

    /// <summary>false — события не уходят в Kafka (только SignalR), удобно для локальной отладки.</summary>
    public bool Enabled { get; set; } = true;

    public string BootstrapServers { get; set; } = "localhost:9092";

    /// <summary>Префикс топиков, например "parking.". Итоговое имя: префикс + Topic события.</summary>
    public string TopicPrefix { get; set; } = string.Empty;

    public int MaxAttempts { get; set; } = 5;

    public int BatchSize { get; set; } = 100;

    public int PollIntervalMs { get; set; } = 1000;

    /// <summary>Сколько секунд событие может ждать в outbox, прежде чем здоровье станет Degraded.</summary>
    public int MaxLagSeconds { get; set; } = 60;
}

/// <summary>Транспорт сообщений. Подменяется в тестах.</summary>
public interface IMessageBus
{
    Task PublishAsync(string topic, string key, string message, CancellationToken cancellationToken);
}

internal sealed partial class KafkaMessageBus : IMessageBus, IDisposable
{
    private readonly IProducer<string, string> _producer;
    private readonly string _prefix;

    public KafkaMessageBus(IOptions<KafkaOptions> options, ILogger<KafkaMessageBus> logger)
    {
        var o = options.Value;
        _prefix = o.TopicPrefix;
        var config = new ProducerConfig
        {
            BootstrapServers = o.BootstrapServers,
            Acks = Acks.All,
            EnableIdempotence = true,
            MessageTimeoutMs = 10_000,
        };
        _producer = new ProducerBuilder<string, string>(config)
            .SetErrorHandler((_, e) => LogKafkaError(logger, e.Code, e.Reason))
            .Build();
    }

    public async Task PublishAsync(string topic, string key, string message, CancellationToken cancellationToken)
    {
        await _producer.ProduceAsync(_prefix + topic, new Message<string, string> { Key = key, Value = message }, cancellationToken);
    }

    public void Dispose()
    {
        _producer.Flush(TimeSpan.FromSeconds(5));
        _producer.Dispose();
    }

    [LoggerMessage(Level = LogLevel.Warning, Message = "Kafka: {Code} {Reason}")]
    private static partial void LogKafkaError(ILogger logger, ErrorCode code, string reason);
}

/// <summary>Используется при Kafka:Enabled = false.</summary>
internal sealed partial class NoOpMessageBus(ILogger<NoOpMessageBus> logger) : IMessageBus
{
    public Task PublishAsync(string topic, string key, string message, CancellationToken cancellationToken)
    {
        LogSkipped(logger, topic, key);
        return Task.CompletedTask;
    }

    [LoggerMessage(Level = LogLevel.Debug, Message = "Kafka отключена, сообщение в {Topic} (key={Key}) не отправлено")]
    private static partial void LogSkipped(ILogger logger, string topic, string key);
}
