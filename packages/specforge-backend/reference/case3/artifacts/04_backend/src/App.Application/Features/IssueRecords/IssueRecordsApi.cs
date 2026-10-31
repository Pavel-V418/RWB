using App.Application.Abstractions;
using App.Application.Common;
using App.Application.Contracts;
using App.Domain.Common;
using App.Domain.Entities;
using Microsoft.EntityFrameworkCore;
using System.Text.Json;

namespace App.Application.Features.IssueRecords;

internal sealed class IssueRecordsApi(
    IAppDbContext db,
    ICurrentUser currentUser,
    IOutbox outbox,
    TimeProvider time) : IIssueRecordsApi
{
    public async Task<ListIssueRecordsResponseDto> ListIssueRecordsAsync(
        Guid? toolId,
        Guid? employeeId,
        ListIssueRecordsStatus? status,
        bool? overdueOnly,
        int page,
        int pageSize,
        CancellationToken cancellationToken)
    {
        if (page < 1) page = 1;
        if (pageSize < 1) pageSize = 20;
        if (pageSize > 200) pageSize = 200;

        var now = time.GetUtcNow();

        var query = db.IssueRecords.AsNoTracking().AsQueryable();

        if (toolId.HasValue)
        {
            query = query.Where(r => r.ToolId == toolId.Value);
        }

        if (employeeId.HasValue)
        {
            query = query.Where(r => r.EmployeeId == employeeId.Value);
        }

        if (status.HasValue)
        {
            var statusValue = ContractEnums.ToValue(status.Value);
            query = query.Where(r => r.Status == statusValue);
        }

        if (overdueOnly == true)
        {
            query = query.Where(r => r.Status == "overdue" || r.ReturnDeadline < now);
        }

        var total = await query.CountAsync(cancellationToken);

        var items = await query
            .OrderByDescending(r => r.CreatedAt)
            .Skip((page - 1) * pageSize)
            .Take(pageSize)
            .ToListAsync(cancellationToken);

        return new ListIssueRecordsResponseDto
        {
            Items = items.Select(ToDto).ToList(),
            Page = page,
            PageSize = pageSize,
            Total = total
        };
    }

    public async Task<IssueRecordDto> IssueToolAsync(IssueToolRequestDto body, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(body);

        var now = time.GetUtcNow();

        if (body.IssueDate > now)
        {
            throw new RequestValidationException("IssueDate", "Дата выдачи не может быть в будущем.");
        }

        if (body.ReturnDeadline <= body.IssueDate)
        {
            throw new RequestValidationException("ReturnDeadline", "Срок возврата должен быть позже даты выдачи.");
        }

        var tool = await db.Tools
            .FirstOrDefaultAsync(t => t.Id == body.ToolId, cancellationToken)
            ?? throw new NotFoundException("Инструмент", body.ToolId);

        var employee = await db.Employees
            .FirstOrDefaultAsync(e => e.Id == body.EmployeeId, cancellationToken)
            ?? throw new NotFoundException("Сотрудник", body.EmployeeId);

        if (tool.Status == "issued")
        {
            throw new ConflictException("tool_already_issued", "Инструмент уже выдан.");
        }

        var activeReservation = await db.Reservations
            .FirstOrDefaultAsync(r => r.ToolId == body.ToolId && r.Status == "active", cancellationToken);

        if (activeReservation is not null && activeReservation.EmployeeId != body.EmployeeId)
        {
            throw new ConflictException("tool_reserved_by_other", "Инструмент забронирован другим сотрудником.");
        }

        var issueRecord = new IssueRecord
        {
            Id = Guid.NewGuid(),
            ToolId = body.ToolId,
            EmployeeId = body.EmployeeId,
            IssueDate = body.IssueDate,
            ReturnDeadline = body.ReturnDeadline,
            Status = "active",
            CreatedAt = now,
            UpdatedAt = now
        };

        db.IssueRecords.Add(issueRecord);

        tool.Status = "issued";
        tool.UpdatedAt = now;

        if (activeReservation is not null)
        {
            activeReservation.Status = "fulfilled";
            activeReservation.UpdatedAt = now;
        }

        var movementHistory = new MovementHistory
        {
            Id = Guid.NewGuid(),
            ToolId = body.ToolId,
            EventType = "issued",
            Timestamp = now,
            UserId = currentUser.UserId,
            Payload = JsonSerializer.Serialize(new { ToolId = body.ToolId, EmployeeId = body.EmployeeId }),
            CreatedAt = now,
            UpdatedAt = now
        };

        db.MovementHistories.Add(movementHistory);

        var auditLog = new AuditLog
        {
            Id = Guid.NewGuid(),
            UserId = currentUser.UserId,
            Action = "issue_tool",
            EntityType = "IssueRecord",
            EntityId = issueRecord.Id,
            Timestamp = now,
            Details = JsonSerializer.Serialize(new { ToolId = body.ToolId, EmployeeId = body.EmployeeId }),
            CreatedAt = now,
            UpdatedAt = now
        };

        db.AuditLogs.Add(auditLog);

        outbox.Enqueue(new ToolIssued(
            issueRecord.Id,
            body.ToolId,
            body.EmployeeId,
            body.IssueDate,
            body.ReturnDeadline,
            now));

        await db.SaveChangesAsync(cancellationToken);

        return ToDto(issueRecord);
    }

    private static IssueRecordDto ToDto(IssueRecord record) => new()
    {
        Id = record.Id,
        ToolId = record.ToolId,
        EmployeeId = record.EmployeeId,
        IssueDate = record.IssueDate,
        ReturnDeadline = record.ReturnDeadline,
        Status = ContractEnums.Parse<IssueRecordStatus>(record.Status),
        CreatedAt = record.CreatedAt,
        UpdatedAt = record.UpdatedAt
    };

    internal sealed record ToolIssued(
        Guid IssueRecordId,
        Guid ToolId,
        Guid EmployeeId,
        DateTimeOffset IssueDate,
        DateTimeOffset ReturnDeadline,
        DateTimeOffset OccurredAt) : IIntegrationEvent
    {
        public string EventType => "ToolIssued";
        public int Version => 1;
        public string Topic => "tools";
        public string PartitionKey => ToolId.ToString();
    }
}
