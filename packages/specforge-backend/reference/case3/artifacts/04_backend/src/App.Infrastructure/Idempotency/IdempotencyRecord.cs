using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata.Builders;

namespace App.Infrastructure.Idempotency;

/// <summary>Сохранённый ответ на запрос с заголовком Idempotency-Key.</summary>
public sealed class IdempotencyRecord
{
    public Guid UserId { get; set; }

    public string Key { get; set; } = string.Empty;

    public string RequestHash { get; set; } = string.Empty;

    public short ResponseStatus { get; set; }

    public string? ResponseBody { get; set; }

    public string? ResponseContentType { get; set; }

    public DateTimeOffset CreatedAt { get; set; }
}

internal sealed class IdempotencyRecordConfiguration : IEntityTypeConfiguration<IdempotencyRecord>
{
    public void Configure(EntityTypeBuilder<IdempotencyRecord> builder)
    {
        builder.ToTable("idempotency_records");
        builder.HasKey(x => new { x.UserId, x.Key });
        builder.Property(x => x.Key).HasMaxLength(64);
        builder.Property(x => x.RequestHash).HasMaxLength(64).IsFixedLength();
        builder.Property(x => x.ResponseContentType).HasMaxLength(100);
    }
}
