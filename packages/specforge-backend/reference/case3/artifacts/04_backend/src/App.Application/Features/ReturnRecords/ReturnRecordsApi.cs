using App.Application.Abstractions;
using App.Application.Common;
using App.Application.Contracts;
using App.Domain.Common;
using App.Domain.Entities;
using Microsoft.EntityFrameworkCore;
using System.Text.Json;
using Microsoft.Extensions.Logging;

namespace App.Application.Features.ReturnRecords;

/// <summary>
/// Реализация операций тега ReturnRecords: список записей о возврате и фиксация возврата инструмента.
/// </summary>
internal sealed class ReturnRecordsApi(
    IAppDbContext db,
    ICurrentUser currentUser,
    IOutbox outbox,
    TimeProvider time,
    ILogger<ReturnRecordsApi> logger) : IReturnRecordsApi
{
    public async Task<ListReturnRecordsResponseDto> ListReturnRecordsAsync(
        Guid? toolId,
        Guid? employeeId,
        ListReturnRecordsCondition? condition,
        int page,
        int pageSize,
        CancellationToken cancellationToken)
    {
        if (page < 1) page = 1;
        if (pageSize < 1) pageSize = 1;
        if (pageSize > 200) pageSize = 200;

        var query = db.ReturnRecords.AsNoTracking();

        if (toolId.HasValue)
        {
            query = query.Where(r => r.ToolId == toolId.Value);
        }

        if (employeeId.HasValue)
        {
            query = query.Where(r => r.EmployeeId == employeeId.Value);
        }

        if (condition.HasValue)
        {
            var conditionValue = ContractEnums.ToValue(condition.Value);
            query = query.Where(r => r.Condition == conditionValue);
        }

        var total = await query.CountAsync(cancellationToken);

        var items = await query
            .OrderByDescending(r => r.ReturnDate)
            .Skip((page - 1) * pageSize)
            .Take(pageSize)
            .ToListAsync(cancellationToken);

        return new ListReturnRecordsResponseDto
        {
            Items = items.Select(ToDto).ToList(),
            Page = page,
            PageSize = pageSize,
            Total = total
        };
    }

    public async Task<ReturnRecordDto> ReturnToolAsync(
        ReturnToolRequestDto body,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(body);

        var now = time.GetUtcNow();

        // Валидация: returnDate не может быть в будущем
        if (body.ReturnDate > now)
        {
            throw new RequestValidationException("ReturnDate", "Дата возврата не может быть в будущем.");
        }

        // Проверка существования записи о выдаче
        var issueRecord = await db.IssueRecords
            .FirstOrDefaultAsync(ir => ir.Id == body.IssueRecordId, cancellationToken)
            ?? throw new NotFoundException("Запись о выдаче", body.IssueRecordId);

        // Проверка существования инструмента
        var tool = await db.Tools
            .FirstOrDefaultAsync(t => t.Id == body.ToolId, cancellationToken)
            ?? throw new NotFoundException("Инструмент", body.ToolId);

        // Проверка существования сотрудника
        var employee = await db.Employees
            .FirstOrDefaultAsync(e => e.Id == body.EmployeeId, cancellationToken)
            ?? throw new NotFoundException("Сотрудник", body.EmployeeId);

        // Проверка статуса записи о выдаче: должна быть active или overdue
        var issueStatus = ContractEnums.Parse<IssueRecordStatus>(issueRecord.Status);
        if (issueStatus != IssueRecordStatus.Active && issueStatus != IssueRecordStatus.Overdue)
        {
            throw new ConflictException("issue_record_already_returned", "Запись о выдаче уже возвращена.");
        }

        // Проверка, что инструмент и сотрудник соответствуют записи о выдаче
        if (issueRecord.ToolId != body.ToolId)
        {
            throw new RequestValidationException("ToolId", "Инструмент не соответствует записи о выдаче.");
        }

        if (issueRecord.EmployeeId != body.EmployeeId)
        {
            throw new RequestValidationException("EmployeeId", "Сотрудник не соответствует записи о выдаче.");
        }

        // Определяем новый статус инструмента в зависимости от condition
        var conditionValue = ContractEnums.ToValue(body.Condition);
        var newToolStatus = body.Condition switch
        {
            ReturnToolRequestCondition.Working => "available",
            ReturnToolRequestCondition.NeedsRepair => "repair",
            ReturnToolRequestCondition.Lost => "lost",
            _ => throw new BusinessRuleException("invalid_condition", "Недопустимое состояние возврата.")
        };

        // Создаём запись о возврате
        var returnRecord = new ReturnRecord
        {
            Id = Guid.NewGuid(),
            IssueRecordId = body.IssueRecordId,
            ToolId = body.ToolId,
            EmployeeId = body.EmployeeId,
            ReturnDate = body.ReturnDate,
            Condition = conditionValue,
            CreatedAt = now,
            UpdatedAt = now
        };

        // Обновляем статус записи о выдаче на returned
        issueRecord.Status = ContractEnums.ToValue(IssueRecordStatus.Returned);
        issueRecord.UpdatedAt = now;

        // Обновляем статус инструмента
        tool.Status = newToolStatus;
        tool.UpdatedAt = now;

        // Фиксируем событие returned в MovementHistory
        var movementHistory = new MovementHistory
        {
            Id = Guid.NewGuid(),
            ToolId = body.ToolId,
            EventType = "returned",
            Timestamp = now,
            UserId = currentUser.UserId,
            Payload = JsonSerializer.Serialize(new
            {
                IssueRecordId = body.IssueRecordId,
                EmployeeId = body.EmployeeId,
                Condition = conditionValue,
                ReturnDate = body.ReturnDate
            }),
            CreatedAt = now,
            UpdatedAt = now
        };

        // Фиксируем действие в AuditLog
        var auditLog = new AuditLog
        {
            Id = Guid.NewGuid(),
            UserId = currentUser.UserId,
            Action = "return_tool",
            EntityType = "ReturnRecord",
            EntityId = returnRecord.Id,
            Timestamp = now,
            Details = JsonSerializer.Serialize(new
            {
                IssueRecordId = body.IssueRecordId,
                ToolId = body.ToolId,
                EmployeeId = body.EmployeeId,
                Condition = conditionValue,
                ReturnDate = body.ReturnDate
            }),
            CreatedAt = now,
            UpdatedAt = now
        };

        // Публикуем событие tool.returned в Kafka через outbox
        outbox.Enqueue(new ToolReturned(
            returnRecord.Id,
            body.ToolId,
            body.EmployeeId,
            body.IssueRecordId,
            conditionValue,
            body.ReturnDate,
            now));

        // Добавляем все сущности в контекст
        db.ReturnRecords.Add(returnRecord);
        db.MovementHistories.Add(movementHistory);
        db.AuditLogs.Add(auditLog);

        await db.SaveChangesAsync(cancellationToken);

        if (logger.IsEnabled(LogLevel.Information))
        {
            logger.LogInformation("Инструмент {ToolId} возвращён. Запись о возврате: {ReturnRecordId}", body.ToolId, returnRecord.Id);
        }

        return ToDto(returnRecord);
    }

    private static ReturnRecordDto ToDto(ReturnRecord entity) => new()
    {
        Id = entity.Id,
        IssueRecordId = entity.IssueRecordId,
        ToolId = entity.ToolId,
        EmployeeId = entity.EmployeeId,
        ReturnDate = entity.ReturnDate,
        Condition = ContractEnums.Parse<ReturnRecordCondition>(entity.Condition),
        CreatedAt = entity.CreatedAt,
        UpdatedAt = entity.UpdatedAt
    };

    private enum IssueRecordStatus
    {
        Active,
        Returned,
        Overdue
    }

    /// <summary>
    /// Событие возврата инструмента.
    /// </summary>
    private sealed record ToolReturned(
        Guid ReturnRecordId,
        Guid ToolId,
        Guid EmployeeId,
        Guid IssueRecordId,
        string Condition,
        DateTimeOffset ReturnDate,
        DateTimeOffset OccurredAt) : IIntegrationEvent
    {
        public string EventType => "tool.returned";
        public int Version => 1;
        public string Topic => "tools";
        public string PartitionKey => ToolId.ToString();
    }
}
