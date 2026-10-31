using System.Text.Json;
using System.Text.Json.Nodes;
using App.Application.Abstractions;
using App.Application.Common;
using App.Application.Contracts;
using App.Domain.Entities;
using Microsoft.EntityFrameworkCore;

namespace App.Application.Features.MovementHistoryFeature;

/// <summary>
/// Реализация операций тега MovementHistory: история движения инструмента.
/// </summary>
internal sealed class MovementHistoryApi(
    IAppDbContext db,
    ICurrentUser currentUser) : IMovementHistoryApi
{
    public async Task<GetToolMovementHistoryResponseDto> GetToolMovementHistoryAsync(
        Guid id,
        int page,
        int pageSize,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(db);
        ArgumentNullException.ThrowIfNull(currentUser);

        if (page < 1)
        {
            throw new RequestValidationException("page", "Номер страницы должен быть больше или равен 1.");
        }

        if (pageSize < 1 || pageSize > 200)
        {
            throw new RequestValidationException("pageSize", "Размер страницы должен быть от 1 до 200.");
        }

        var tool = await db.Tools.AsNoTracking()
            .FirstOrDefaultAsync(t => t.Id == id, cancellationToken);

        if (tool is null)
        {
            throw new NotFoundException("Инструмент", id);
        }

        var query = db.MovementHistories.AsNoTracking()
            .Where(m => m.ToolId == id)
            .OrderByDescending(m => m.Timestamp)
            .ThenByDescending(m => m.CreatedAt);

        var total = await query.CountAsync(cancellationToken);

        var items = await query
            .Skip((page - 1) * pageSize)
            .Take(pageSize)
            .ToListAsync(cancellationToken);

        return new GetToolMovementHistoryResponseDto
        {
            Items = items.Select(ToDto).ToList(),
            Page = page,
            PageSize = pageSize,
            Total = total,
        };
    }

    private static MovementHistoryDto ToDto(MovementHistory entity)
    {
        JsonObject? payload = null;
        if (!string.IsNullOrWhiteSpace(entity.Payload))
        {
            try
            {
                payload = JsonSerializer.Deserialize<JsonObject>(entity.Payload);
            }
            catch (JsonException)
            {
                payload = null;
            }
        }

        return new MovementHistoryDto
        {
            Id = entity.Id,
            ToolId = entity.ToolId,
            EventType = ContractEnums.Parse<MovementHistoryEventType>(entity.EventType),
            Timestamp = entity.Timestamp,
            UserId = entity.UserId,
            Payload = payload,
            CreatedAt = entity.CreatedAt,
            UpdatedAt = entity.UpdatedAt,
        };
    }
}
