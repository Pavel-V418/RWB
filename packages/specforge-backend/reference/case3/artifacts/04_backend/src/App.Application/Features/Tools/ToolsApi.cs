using System.Text.Json;
using System.Text.RegularExpressions;
using App.Application.Abstractions;
using App.Application.Common;
using App.Application.Contracts;
using App.Domain.Common;
using App.Domain.Entities;
using Microsoft.EntityFrameworkCore;

namespace App.Application.Features.Tools;

/// <summary>
/// Реализация операций каталога инструментов: список, создание, чтение по ID/QR, списание.
/// </summary>
internal sealed class ToolsApi(
    IAppDbContext db,
    ICurrentUser currentUser,
    IOutbox outbox,
    TimeProvider time) : IToolsApi
{
    private static readonly Regex InventoryNumberRegex = new("^[A-Z0-9-]{3,50}$", RegexOptions.Compiled);

    public async Task<ListToolsResponseDto> ListToolsAsync(
        string? category,
        string? warehouse,
        ListToolsStatus? status,
        int page,
        int pageSize,
        CancellationToken cancellationToken)
    {
        if (page < 1)
        {
            throw new RequestValidationException("page", "Номер страницы должен быть больше или равен 1.");
        }

        if (pageSize < 1 || pageSize > 200)
        {
            throw new RequestValidationException("pageSize", "Размер страницы должен быть от 1 до 200.");
        }

        var query = db.Tools.AsNoTracking().AsQueryable();

        if (!string.IsNullOrWhiteSpace(category))
        {
            query = query.Where(t => t.Category == category);
        }

        if (!string.IsNullOrWhiteSpace(warehouse))
        {
            query = query.Where(t => t.Warehouse == warehouse);
        }

        if (status is not null)
        {
            var statusValue = ContractEnums.ToValue(status.Value);
            query = query.Where(t => t.Status == statusValue);
        }

        var total = await query.CountAsync(cancellationToken);

        var items = await query
            .OrderBy(t => t.CreatedAt)
            .Skip((page - 1) * pageSize)
            .Take(pageSize)
            .ToListAsync(cancellationToken);

        return new ListToolsResponseDto
        {
            Items = items.Select(ToDto).ToList(),
            Page = page,
            PageSize = pageSize,
            Total = total,
        };
    }

    public async Task<ToolDto> CreateToolAsync(CreateToolRequestDto body, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(body);

        if (!InventoryNumberRegex.IsMatch(body.InventoryNumber))
        {
            throw new RequestValidationException("InventoryNumber", "Инвентарный номер должен соответствовать формату: 3-50 символов, только A-Z, 0-9 и дефис.");
        }

        if (string.IsNullOrWhiteSpace(body.Name) || body.Name.Length > 200)
        {
            throw new RequestValidationException("Name", "Наименование обязательно и не должно превышать 200 символов.");
        }

        if (string.IsNullOrWhiteSpace(body.Category) || body.Category.Length > 100)
        {
            throw new RequestValidationException("Category", "Категория обязательна и не должна превышать 100 символов.");
        }

        if (string.IsNullOrWhiteSpace(body.Warehouse) || body.Warehouse.Length > 100)
        {
            throw new RequestValidationException("Warehouse", "Склад обязателен и не должен превышать 100 символов.");
        }

        var existing = await db.Tools.AsNoTracking()
            .AnyAsync(t => t.InventoryNumber == body.InventoryNumber, cancellationToken);

        if (existing)
        {
            throw new ConflictException("inventory_number_exists", "Инструмент с таким инвентарным номером уже существует.");
        }

        var now = time.GetUtcNow();
        var statusValue = ContractEnums.ToValue(body.Status);
        var qrCode = GenerateQrCode(body.InventoryNumber);

        var tool = new Tool
        {
            Id = Guid.NewGuid(),
            InventoryNumber = body.InventoryNumber,
            Name = body.Name,
            Category = body.Category,
            Warehouse = body.Warehouse,
            Status = statusValue,
            QrCode = qrCode,
            CreatedAt = now,
            UpdatedAt = now,
        };

        db.Tools.Add(tool);

        var movementHistory = new MovementHistory
        {
            Id = Guid.NewGuid(),
            ToolId = tool.Id,
            EventType = "created",
            Timestamp = now,
            UserId = currentUser.UserId,
            Payload = JsonSerializer.Serialize(new { tool.InventoryNumber, tool.Name }),
            CreatedAt = now,
            UpdatedAt = now,
        };
        db.MovementHistories.Add(movementHistory);

        var auditLog = new AuditLog
        {
            Id = Guid.NewGuid(),
            UserId = currentUser.UserId,
            Action = "tool_created",
            EntityType = "Tool",
            EntityId = tool.Id,
            Timestamp = now,
            Details = JsonSerializer.Serialize(new { tool.InventoryNumber, tool.Name }),
            CreatedAt = now,
            UpdatedAt = now,
        };
        db.AuditLogs.Add(auditLog);

        outbox.Enqueue(new ToolCreated(tool.Id, tool.InventoryNumber, now));

        await db.SaveChangesAsync(cancellationToken);

        return ToDto(tool);
    }

    public async Task<ToolDto> GetToolAsync(Guid id, CancellationToken cancellationToken)
    {
        var tool = await db.Tools.AsNoTracking()
            .FirstOrDefaultAsync(t => t.Id == id, cancellationToken)
            ?? throw new NotFoundException("Инструмент", id);

        return ToDto(tool);
    }

    public async Task<ToolDto> GetToolByQrCodeAsync(string qrCode, CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(qrCode))
        {
            throw new RequestValidationException("qrCode", "QR-код не может быть пустым.");
        }

        var tool = await db.Tools.AsNoTracking()
            .FirstOrDefaultAsync(t => t.QrCode == qrCode, cancellationToken)
            ?? throw new NotFoundException("Инструмент", qrCode);

        return ToDto(tool);
    }

    public async Task<ToolDto> RetireToolAsync(Guid id, CancellationToken cancellationToken)
    {
        var tool = await db.Tools
            .FirstOrDefaultAsync(t => t.Id == id, cancellationToken)
            ?? throw new NotFoundException("Инструмент", id);

        if (tool.Status == ContractEnums.ToValue(ToolStatus.Issued))
        {
            throw new ConflictException("tool_is_issued", "Инструмент не может быть списан, так как он выдан сотруднику.");
        }

        var now = time.GetUtcNow();
        tool.Status = ContractEnums.ToValue(ToolStatus.Retired);
        tool.UpdatedAt = now;

        var movementHistory = new MovementHistory
        {
            Id = Guid.NewGuid(),
            ToolId = tool.Id,
            EventType = "retired",
            Timestamp = now,
            UserId = currentUser.UserId,
            Payload = JsonSerializer.Serialize(new { tool.InventoryNumber }),
            CreatedAt = now,
            UpdatedAt = now,
        };
        db.MovementHistories.Add(movementHistory);

        var auditLog = new AuditLog
        {
            Id = Guid.NewGuid(),
            UserId = currentUser.UserId,
            Action = "tool_retired",
            EntityType = "Tool",
            EntityId = tool.Id,
            Timestamp = now,
            Details = JsonSerializer.Serialize(new { tool.InventoryNumber }),
            CreatedAt = now,
            UpdatedAt = now,
        };
        db.AuditLogs.Add(auditLog);

        outbox.Enqueue(new ToolRetired(tool.Id, tool.InventoryNumber, now));

        await db.SaveChangesAsync(cancellationToken);

        return ToDto(tool);
    }

    private static string GenerateQrCode(string inventoryNumber)
    {
        return $"TOOL-{inventoryNumber}";
    }

    private static ToolDto ToDto(Tool tool) => new()
    {
        Id = tool.Id,
        InventoryNumber = tool.InventoryNumber,
        Name = tool.Name,
        Category = tool.Category,
        Warehouse = tool.Warehouse,
        Status = ContractEnums.Parse<ToolStatus>(tool.Status),
        QrCode = tool.QrCode,
        CreatedAt = tool.CreatedAt,
        UpdatedAt = tool.UpdatedAt,
    };

    internal sealed record ToolCreated(Guid ToolId, string InventoryNumber, DateTimeOffset OccurredAt) : IIntegrationEvent
    {
        public string EventType => "ToolCreated";
        public int Version => 1;
        public string Topic => "tools";
        public string PartitionKey => ToolId.ToString();
    }

    internal sealed record ToolRetired(Guid ToolId, string InventoryNumber, DateTimeOffset OccurredAt) : IIntegrationEvent
    {
        public string EventType => "ToolRetired";
        public int Version => 1;
        public string Topic => "tools";
        public string PartitionKey => ToolId.ToString();
    }
}
