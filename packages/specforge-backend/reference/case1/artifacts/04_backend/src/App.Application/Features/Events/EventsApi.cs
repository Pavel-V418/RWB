using System.Text.Json;
using System.Text.Json.Nodes;
using App.Application.Abstractions;
using App.Application.Common;
using App.Application.Contracts;
using App.Domain.Entities;
using Microsoft.EntityFrameworkCore;

namespace App.Application.Features.Events;

/// <summary>
/// Реализация I&lt;Events&gt;Api: журнал доменных событий для административного интерфейса.
/// </summary>
internal sealed class EventsApi(
    IAppDbContext db,
    ICurrentUser currentUser) : IEventsApi
{
    public async Task<DomainEventPageDto> ListEventsAsync(
        Guid? vehicleId,
        EventType? type,
        DateTimeOffset? from,
        DateTimeOffset? to,
        int page,
        int pageSize,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(db);
        ArgumentNullException.ThrowIfNull(currentUser);

        if (page < 1)
        {
            throw new BusinessRuleException("invalid_page", "Номер страницы должен быть больше или равен 1.");
        }

        if (pageSize < 1 || pageSize > 100)
        {
            throw new BusinessRuleException("invalid_page_size", "Размер страницы должен быть в диапазоне от 1 до 100.");
        }

        if (from is not null && to is not null && from.Value > to.Value)
        {
            throw new BusinessRuleException("invalid_date_range", "Дата начала должна быть раньше или равна дате окончания.");
        }

        var query = db.DomainEvents.AsNoTracking().AsQueryable();

        if (vehicleId is not null)
        {
            query = query.Where(e => e.VehicleId == vehicleId);
        }

        if (type is not null)
        {
            var typeValue = ContractEnums.ToValue(type.Value);
            query = query.Where(e => e.EventType == typeValue);
        }

        if (from is not null)
        {
            query = query.Where(e => e.OccurredAt >= from.Value);
        }

        if (to is not null)
        {
            query = query.Where(e => e.OccurredAt <= to.Value);
        }

        var total = await query.CountAsync(cancellationToken);

        var items = await query
            .OrderByDescending(e => e.OccurredAt)
            .Skip((page - 1) * pageSize)
            .Take(pageSize)
            .ToListAsync(cancellationToken);

        return new DomainEventPageDto
        {
            Items = items.Select(ToDto).ToList(),
            Meta = new PageMetaDto
            {
                Page = page,
                PageSize = pageSize,
                Total = total
            }
        };
    }

    private static DomainEventDto ToDto(DomainEvent entity)
    {
        var eventType = ContractEnums.Parse<EventType>(entity.EventType);
        
        JsonObject payload;
        try
        {
            payload = JsonSerializer.Deserialize<JsonObject>(entity.Payload) ?? new JsonObject();
        }
        catch (JsonException)
        {
            payload = new JsonObject();
        }

        return new DomainEventDto
        {
            EventId = entity.Id,
            Type = eventType,
            Version = entity.EventVersion,
            OccurredAt = entity.OccurredAt,
            VehicleId = entity.VehicleId,
            SessionId = entity.SessionId,
            ZoneId = entity.ZoneId,
            Payload = payload
        };
    }
}
