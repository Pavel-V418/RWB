using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata.Builders;

namespace App.Infrastructure.Messaging;

/// <summary>Строка таблицы outbox_messages.</summary>
public sealed class OutboxMessage
{
    public Guid Id { get; set; }

    public string EventType { get; set; } = string.Empty;

    public short EventVersion { get; set; }

    public string Topic { get; set; } = string.Empty;

    public string PartitionKey { get; set; } = string.Empty;

    public DateTimeOffset OccurredAt { get; set; }

    /// <summary>Сериализованная оболочка <see cref="EventEnvelope"/>.</summary>
    public string Envelope { get; set; } = string.Empty;

    public DateTimeOffset CreatedAt { get; set; }

    public DateTimeOffset? PublishedAt { get; set; }

    public short Attempts { get; set; }

    public string? LastError { get; set; }

    public DateTimeOffset? DeadLetteredAt { get; set; }
}

internal sealed class OutboxMessageConfiguration : IEntityTypeConfiguration<OutboxMessage>
{
    public void Configure(EntityTypeBuilder<OutboxMessage> builder)
    {
        builder.ToTable("outbox_messages");
        builder.HasKey(x => x.Id);
        builder.Property(x => x.EventType).HasMaxLength(64);
        builder.Property(x => x.Topic).HasMaxLength(128);
        builder.Property(x => x.PartitionKey).HasMaxLength(128);
        builder.Property(x => x.Envelope).HasColumnType("jsonb");
        builder.Property(x => x.LastError).HasMaxLength(1000);
    }
}
