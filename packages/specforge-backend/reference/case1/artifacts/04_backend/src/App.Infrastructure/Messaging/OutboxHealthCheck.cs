using App.Infrastructure.Persistence;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Diagnostics.HealthChecks;
using Microsoft.Extensions.Options;

namespace App.Infrastructure.Messaging;

/// <summary>
/// Бизнес-здоровье доставки событий. Degraded, если события застряли в outbox дольше
/// <see cref="KafkaOptions.MaxLagSeconds"/> или за последний час были отправки в DLQ.
/// API при этом продолжает работать, поэтому это не Unhealthy.
/// </summary>
internal sealed class OutboxHealthCheck(AppDbContext db, IOptions<KafkaOptions> options) : IHealthCheck
{
    // created_at ставит БД по реальным часам — сравниваем с ними же, а не с ускоренным TIME_SCALE временем
    private static readonly TimeProvider Clock = TimeProvider.System;

    public async Task<HealthCheckResult> CheckHealthAsync(HealthCheckContext context, CancellationToken cancellationToken = default)
    {
        var now = Clock.GetUtcNow();
        var lagThreshold = now.AddSeconds(-options.Value.MaxLagSeconds);
        var dlqWindow = now.AddHours(-1);

        var pending = db.OutboxMessages.AsNoTracking().Where(m => m.PublishedAt == null && m.DeadLetteredAt == null);
        var stuck = await pending.CountAsync(m => m.CreatedAt < lagThreshold, cancellationToken);
        var deadLettered = await db.OutboxMessages.AsNoTracking().CountAsync(m => m.DeadLetteredAt > dlqWindow, cancellationToken);

        var data = new Dictionary<string, object> { ["stuck"] = stuck, ["deadLetteredLastHour"] = deadLettered };
        return stuck > 0 || deadLettered > 0
            ? HealthCheckResult.Degraded($"Outbox: {stuck} событий ждут дольше {options.Value.MaxLagSeconds} с, {deadLettered} в DLQ за час.", data: data)
            : HealthCheckResult.Healthy("Outbox: доставка в норме.", data);
    }
}
