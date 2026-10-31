using App.Application.Abstractions;
using App.Application.Common;
using App.Application.Contracts;
using App.Domain.Entities;
using Microsoft.EntityFrameworkCore;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace App.Application.Features.AuditLogs;

/// <summary>
/// Реализация <see cref="IAuditLogsApi"/>: чтение журнала аудита с фильтрами и пагинацией.
/// </summary>
internal sealed class AuditLogsApi(
    IAppDbContext db,
    ICurrentUser currentUser) : IAuditLogsApi
{
    public async Task<ListAuditLogsResponseDto> ListAuditLogsAsync(
        Guid? userId,
        string? entityType,
        DateTimeOffset? from,
        DateTimeOffset? to,
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

        if (from is not null && to is not null && from > to)
        {
            throw new RequestValidationException("from", "Начало периода не может быть позже конца периода.");
        }

        var query = db.AuditLogs.AsNoTracking();

        if (userId is not null)
        {
            query = query.Where(a => a.UserId == userId);
        }

        if (!string.IsNullOrWhiteSpace(entityType))
        {
            query = query.Where(a => a.EntityType == entityType);
        }

        if (from is not null)
        {
            query = query.Where(a => a.Timestamp >= from);
        }

        if (to is not null)
        {
            query = query.Where(a => a.Timestamp <= to);
        }

        var total = await query.CountAsync(cancellationToken);

        var items = await query
            .OrderByDescending(a => a.Timestamp)
            .Skip((page - 1) * pageSize)
            .Take(pageSize)
            .ToListAsync(cancellationToken);

        return new ListAuditLogsResponseDto
        {
            Items = items.Select(ToDto).ToList(),
            Page = page,
            PageSize = pageSize,
            Total = total,
        };
    }

    private static AuditLogDto ToDto(AuditLog entity)
    {
        JsonObject? details = null;
        if (!string.IsNullOrWhiteSpace(entity.Details))
        {
            try
            {
                details = JsonSerializer.Deserialize<JsonObject>(entity.Details);
            }
            catch (JsonException)
            {
                details = null;
            }
        }

        return new AuditLogDto
        {
            Id = entity.Id,
            UserId = entity.UserId,
            Action = entity.Action,
            EntityType = entity.EntityType,
            EntityId = entity.EntityId,
            Timestamp = entity.Timestamp,
            Details = details,
            CreatedAt = entity.CreatedAt,
            UpdatedAt = entity.UpdatedAt,
        };
    }
}
