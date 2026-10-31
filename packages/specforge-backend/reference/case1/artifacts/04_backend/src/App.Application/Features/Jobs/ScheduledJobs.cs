using System.Text.Json;
using App.Application.Abstractions;
using App.Application.Common;
using App.Application.Contracts;
using App.Domain.Common;
using App.Domain.Entities;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Logging;

namespace App.Application.Features.Jobs;

/// <summary>
/// Периодическая задача: предупреждение за 10 минут до окончания оплаченного периода (FR-9).
/// </summary>
internal sealed class SessionExpiryWarningJob(
    IAppDbContext db,
    IOutbox outbox,
    TimeProvider time,
    ILogger<SessionExpiryWarningJob> logger) : IScheduledJob
{
    public string Name => "session-expiry-warning";
    public TimeSpan Interval => TimeSpan.FromMinutes(1);

    public async Task<int> RunAsync(CancellationToken cancellationToken)
    {
        var now = time.GetUtcNow();
        var warningThreshold = now.AddMinutes(10);

        var sessions = await db.ParkingSessions
            .Where(s => s.Status == "active" && s.ExpiryWarnedAt == null && s.PaidUntil <= warningThreshold)
            .Take(100)
            .ToListAsync(cancellationToken);

        if (sessions.Count == 0)
        {
            return 0;
        }

        foreach (var session in sessions)
        {
            session.ExpiryWarnedAt = now;
            session.UpdatedAt = now;

            var domainEvent = new DomainEvent
            {
                Id = Guid.NewGuid(),
                EventType = "SessionExpiringSoon",
                EventVersion = 1,
                OccurredAt = now,
                VehicleId = session.VehicleId,
                SessionId = session.Id,
                ZoneId = session.ZoneId,
                Payload = JsonSerializer.Serialize(new
                {
                    sessionId = session.Id,
                    vehicleId = session.VehicleId,
                    zoneId = session.ZoneId,
                    paidUntil = session.PaidUntil
                }),
                CreatedAt = now
            };

            db.DomainEvents.Add(domainEvent);

            var notification = new Notification
            {
                Id = Guid.NewGuid(),
                VehicleId = session.VehicleId,
                EventId = domainEvent.Id,
                Kind = ContractEnums.ToValue(NotificationKind.SessionExpiring),
                Severity = ContractEnums.ToValue(NotificationSeverity.Warning),
                Title = "Парковка скоро закончится",
                Body = $"Оплаченный период закончится в {session.PaidUntil:HH:mm}. Продлите парковку.",
                CreatedAt = now
            };

            db.Notifications.Add(notification);

            outbox.Enqueue(new SessionExpiryWarningEvent(
                session.Id,
                session.VehicleId,
                session.ZoneId,
                session.PaidUntil,
                now));
        }

        await db.SaveChangesAsync(cancellationToken);
        if (logger.IsEnabled(LogLevel.Information))
        {
            logger.LogInformation("SessionExpiryWarningJob: обработано {Count} сессий", sessions.Count);
        }
        return sessions.Count;
    }
}

/// <summary>
/// Периодическая задача: автоматическое закрытие истёкших сессий (FR-9, FR-10).
/// </summary>
internal sealed class SessionExpiryCloseJob(
    IAppDbContext db,
    IOutbox outbox,
    TimeProvider time,
    ILogger<SessionExpiryCloseJob> logger) : IScheduledJob
{
    public string Name => "session-expiry-close";
    public TimeSpan Interval => TimeSpan.FromMinutes(1);

    public async Task<int> RunAsync(CancellationToken cancellationToken)
    {
        var now = time.GetUtcNow();

        var sessions = await db.ParkingSessions
            .Where(s => s.Status == "active" && s.PaidUntil <= now)
            .Take(100)
            .ToListAsync(cancellationToken);

        if (sessions.Count == 0)
        {
            return 0;
        }

        foreach (var session in sessions)
        {
            session.Status = "expired";
            session.EndedAt = now;
            session.CloseReason = "expired";
            session.UpdatedAt = now;

            var domainEvent = new DomainEvent
            {
                Id = Guid.NewGuid(),
                EventType = "SessionClosed",
                EventVersion = 1,
                OccurredAt = now,
                VehicleId = session.VehicleId,
                SessionId = session.Id,
                ZoneId = session.ZoneId,
                Payload = JsonSerializer.Serialize(new
                {
                    sessionId = session.Id,
                    vehicleId = session.VehicleId,
                    zoneId = session.ZoneId,
                    startedAt = session.StartedAt,
                    endedAt = now,
                    closeReason = "expired"
                }),
                CreatedAt = now
            };

            db.DomainEvents.Add(domainEvent);

            var notification = new Notification
            {
                Id = Guid.NewGuid(),
                VehicleId = session.VehicleId,
                EventId = domainEvent.Id,
                Kind = ContractEnums.ToValue(NotificationKind.SessionExpired),
                Severity = ContractEnums.ToValue(NotificationSeverity.Warning),
                Title = "Парковка истекла",
                Body = "Оплаченный период парковки истёк. Проверьте оплату или оплатите заново.",
                CreatedAt = now
            };

            db.Notifications.Add(notification);

            outbox.Enqueue(new SessionClosedEvent(
                session.Id,
                session.VehicleId,
                session.ZoneId,
                session.StartedAt,
                now,
                SessionCloseReason.Expired,
                now));
        }

        await db.SaveChangesAsync(cancellationToken);
        if (logger.IsEnabled(LogLevel.Information))
        {
            logger.LogInformation("SessionExpiryCloseJob: обработано {Count} сессий", sessions.Count);
        }
        return sessions.Count;
    }
}

/// <summary>
/// Событие предупреждения о скором окончании сессии.
/// </summary>
internal sealed record SessionExpiryWarningEvent(
    Guid SessionId,
    Guid VehicleId,
    Guid ZoneId,
    DateTimeOffset PaidUntil,
    DateTimeOffset OccurredAt) : IIntegrationEvent
{
    public string EventType => "SessionExpiringSoon";
    public int Version => 1;
    public string Topic => "sessions";
    public string PartitionKey => VehicleId.ToString();
}

/// <summary>
/// Событие закрытия сессии (используется при истечении).
/// </summary>
internal sealed record SessionClosedEvent(
    Guid SessionId,
    Guid VehicleId,
    Guid ZoneId,
    DateTimeOffset StartedAt,
    DateTimeOffset EndedAt,
    SessionCloseReason CloseReason,
    DateTimeOffset OccurredAt) : IIntegrationEvent
{
    public string EventType => "SessionClosed";
    public int Version => 1;
    public string Topic => "sessions";
    public string PartitionKey => VehicleId.ToString();
}
