using App.Application.Abstractions;
using App.Application.Common;
using App.Application.Contracts;
using App.Domain.Entities;
using Microsoft.EntityFrameworkCore;
using System.Text.Json;

namespace App.Application.Features.Reservations;

internal sealed class ReservationsApi(
    IAppDbContext db,
    ICurrentUser currentUser,
    TimeProvider time) : IReservationsApi
{
    public async Task<ListReservationsResponseDto> ListReservationsAsync(
        Guid? toolId,
        Guid? employeeId,
        DateOnly? date,
        ListReservationsStatus? status,
        int page,
        int pageSize,
        CancellationToken cancellationToken)
    {
        if (page < 1) page = 1;
        if (pageSize < 1) pageSize = 10;
        if (pageSize > 200) pageSize = 200;

        var query = db.Reservations.AsNoTracking().AsQueryable();

        if (toolId.HasValue)
            query = query.Where(r => r.ToolId == toolId.Value);

        if (employeeId.HasValue)
            query = query.Where(r => r.EmployeeId == employeeId.Value);

        if (date.HasValue)
            query = query.Where(r => r.Date == date.Value);

        if (status.HasValue)
        {
            var statusValue = ContractEnums.ToValue(status.Value);
            query = query.Where(r => r.Status == statusValue);
        }

        var total = await query.CountAsync(cancellationToken);

        var items = await query
            .OrderByDescending(r => r.CreatedAt)
            .Skip((page - 1) * pageSize)
            .Take(pageSize)
            .ToListAsync(cancellationToken);

        return new ListReservationsResponseDto
        {
            Items = items.Select(ToDto).ToList(),
            Page = page,
            PageSize = pageSize,
            Total = total
        };
    }

    public async Task<ReservationDto> CreateReservationAsync(
        CreateReservationRequestDto body,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(body);

        var now = time.GetUtcNow();

        var tool = await db.Tools.AsNoTracking()
            .FirstOrDefaultAsync(t => t.Id == body.ToolId, cancellationToken)
            ?? throw new NotFoundException("Инструмент", body.ToolId);

        var employee = await db.Employees.AsNoTracking()
            .FirstOrDefaultAsync(e => e.Id == body.EmployeeId, cancellationToken)
            ?? throw new NotFoundException("Сотрудник", body.EmployeeId);

        var hasActiveReservation = await db.Reservations.AsNoTracking()
            .AnyAsync(r => r.ToolId == body.ToolId && r.Date == body.Date && r.Status == "active", cancellationToken);

        var hasActiveIssue = await db.IssueRecords.AsNoTracking()
            .AnyAsync(i => i.ToolId == body.ToolId && i.Status == "active"
                && i.IssueDate.Date <= body.Date.ToDateTime(TimeOnly.MinValue)
                && i.ReturnDeadline.Date >= body.Date.ToDateTime(TimeOnly.MinValue), cancellationToken);

        if (hasActiveReservation || hasActiveIssue)
        {
            throw new ConflictException("tool_unavailable", "Инструмент уже забронирован или выдан на указанную дату.");
        }

        var reservation = new Reservation
        {
            Id = Guid.NewGuid(),
            ToolId = body.ToolId,
            EmployeeId = body.EmployeeId,
            Date = body.Date,
            Status = "active",
            CreatedAt = now,
            UpdatedAt = now
        };

        db.Reservations.Add(reservation);

        var toolEntity = await db.Tools.FirstAsync(t => t.Id == body.ToolId, cancellationToken);
        toolEntity.Status = "reserved";
        toolEntity.UpdatedAt = now;

        var movementHistory = new MovementHistory
        {
            Id = Guid.NewGuid(),
            ToolId = body.ToolId,
            EventType = "reserved",
            Timestamp = now,
            UserId = currentUser.UserId,
            Payload = JsonSerializer.Serialize(new { reservationId = reservation.Id, employeeId = body.EmployeeId, date = body.Date }),
            CreatedAt = now,
            UpdatedAt = now
        };
        db.MovementHistories.Add(movementHistory);

        var auditLog = new AuditLog
        {
            Id = Guid.NewGuid(),
            UserId = currentUser.UserId,
            Action = "create_reservation",
            EntityType = "Reservation",
            EntityId = reservation.Id,
            Timestamp = now,
            Details = JsonSerializer.Serialize(new { toolId = body.ToolId, employeeId = body.EmployeeId, date = body.Date }),
            CreatedAt = now,
            UpdatedAt = now
        };
        db.AuditLogs.Add(auditLog);

        await db.SaveChangesAsync(cancellationToken);

        return ToDto(reservation);
    }

    public async Task<ReservationDto> CancelReservationAsync(Guid id, CancellationToken cancellationToken)
    {
        var now = time.GetUtcNow();

        var reservation = await db.Reservations
            .FirstOrDefaultAsync(r => r.Id == id, cancellationToken)
            ?? throw new NotFoundException("Бронирование", id);

        if (reservation.Status != "active")
        {
            throw new ConflictException("reservation_not_active", "Бронирование уже отменено или выполнено.");
        }

        reservation.Status = "cancelled";
        reservation.UpdatedAt = now;

        var hasOtherActiveReservations = await db.Reservations.AsNoTracking()
            .AnyAsync(r => r.ToolId == reservation.ToolId && r.Status == "active" && r.Id != id, cancellationToken);

        var hasActiveIssue = await db.IssueRecords.AsNoTracking()
            .AnyAsync(i => i.ToolId == reservation.ToolId && i.Status == "active", cancellationToken);

        if (!hasOtherActiveReservations && !hasActiveIssue)
        {
            var tool = await db.Tools.FirstAsync(t => t.Id == reservation.ToolId, cancellationToken);
            tool.Status = "available";
            tool.UpdatedAt = now;
        }

        var movementHistory = new MovementHistory
        {
            Id = Guid.NewGuid(),
            ToolId = reservation.ToolId,
            EventType = "reservation_cancelled",
            Timestamp = now,
            UserId = currentUser.UserId,
            Payload = JsonSerializer.Serialize(new { reservationId = id }),
            CreatedAt = now,
            UpdatedAt = now
        };
        db.MovementHistories.Add(movementHistory);

        var auditLog = new AuditLog
        {
            Id = Guid.NewGuid(),
            UserId = currentUser.UserId,
            Action = "cancel_reservation",
            EntityType = "Reservation",
            EntityId = id,
            Timestamp = now,
            Details = JsonSerializer.Serialize(new { toolId = reservation.ToolId, employeeId = reservation.EmployeeId }),
            CreatedAt = now,
            UpdatedAt = now
        };
        db.AuditLogs.Add(auditLog);

        await db.SaveChangesAsync(cancellationToken);

        return ToDto(reservation);
    }

    private static ReservationDto ToDto(Reservation r) => new()
    {
        Id = r.Id,
        ToolId = r.ToolId,
        EmployeeId = r.EmployeeId,
        Date = r.Date,
        Status = ContractEnums.Parse<ReservationStatus>(r.Status),
        CreatedAt = r.CreatedAt,
        UpdatedAt = r.UpdatedAt
    };
}
