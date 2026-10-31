using App.Application.Abstractions;
using App.Application.Common;
using App.Domain.Common;
using App.Domain.Entities;
using Microsoft.EntityFrameworkCore;
using System.Text.Json;

namespace App.Application.Features.Jobs;

/// <summary>
/// Периодическая задача: помечает просроченные выдачи инструмента (FR-7, FR-16).
/// </summary>
internal sealed class OverdueIssueJob(
    IAppDbContext db,
    IOutbox outbox,
    TimeProvider time) : IScheduledJob
{
    public string Name => "overdue-issue";
    public TimeSpan Interval => TimeSpan.FromMinutes(1);

    public async Task<int> RunAsync(CancellationToken cancellationToken)
    {
        var now = time.GetUtcNow();

        var overdueRecords = await db.IssueRecords
            .Where(r => r.Status == "active" && r.ReturnDeadline < now)
            .Take(100)
            .ToListAsync(cancellationToken);

        if (overdueRecords.Count == 0)
        {
            return 0;
        }

        foreach (var record in overdueRecords)
        {
            record.Status = "overdue";
            record.UpdatedAt = now;

            outbox.Enqueue(new ToolOverdue(
                record.Id,
                record.ToolId,
                record.EmployeeId,
                record.ReturnDeadline,
                now));

            db.MovementHistories.Add(new MovementHistory
            {
                Id = Guid.NewGuid(),
                ToolId = record.ToolId,
                EventType = "issued",
                Timestamp = now,
                UserId = null,
                Payload = JsonSerializer.Serialize(new { type = "overdue", issueRecordId = record.Id }),
                CreatedAt = now,
                UpdatedAt = now
            });
        }

        await db.SaveChangesAsync(cancellationToken);
        return overdueRecords.Count;
    }
}

/// <summary>
/// Периодическая задача: закрывает просроченные бронирования (FR-4).
/// </summary>
internal sealed class ExpiredReservationJob(
    IAppDbContext db,
    IOutbox outbox,
    TimeProvider time) : IScheduledJob
{
    public string Name => "expired-reservation";
    public TimeSpan Interval => TimeSpan.FromMinutes(1);

    public async Task<int> RunAsync(CancellationToken cancellationToken)
    {
        var now = time.GetUtcNow();
        var today = DateOnly.FromDateTime(now.DateTime);

        var expiredReservations = await db.Reservations
            .Where(r => r.Status == "active" && r.Date < today)
            .Take(100)
            .ToListAsync(cancellationToken);

        if (expiredReservations.Count == 0)
        {
            return 0;
        }

        foreach (var reservation in expiredReservations)
        {
            reservation.Status = "cancelled";
            reservation.UpdatedAt = now;

            outbox.Enqueue(new ReservationCancelled(
                reservation.Id,
                reservation.ToolId,
                reservation.EmployeeId,
                reservation.Date,
                now));

            db.MovementHistories.Add(new MovementHistory
            {
                Id = Guid.NewGuid(),
                ToolId = reservation.ToolId,
                EventType = "reservation_cancelled",
                Timestamp = now,
                UserId = null,
                Payload = JsonSerializer.Serialize(new { type = "expired", reservationId = reservation.Id }),
                CreatedAt = now,
                UpdatedAt = now
            });
        }

        await db.SaveChangesAsync(cancellationToken);
        return expiredReservations.Count;
    }
}

/// <summary>
/// Событие: инструмент просрочен.
/// </summary>
internal sealed record ToolOverdue(
    Guid IssueRecordId,
    Guid ToolId,
    Guid EmployeeId,
    DateTimeOffset ReturnDeadline,
    DateTimeOffset OccurredAt) : IIntegrationEvent
{
    public string EventType => "ToolOverdue";
    public int Version => 1;
    public string Topic => "tools";
    public string PartitionKey => ToolId.ToString();
}

/// <summary>
/// Событие: бронирование отменено (в т.ч. по истечении срока).
/// </summary>
internal sealed record ReservationCancelled(
    Guid ReservationId,
    Guid ToolId,
    Guid EmployeeId,
    DateOnly Date,
    DateTimeOffset OccurredAt) : IIntegrationEvent
{
    public string EventType => "ReservationCancelled";
    public int Version => 1;
    public string Topic => "tools";
    public string PartitionKey => ToolId.ToString();
}
