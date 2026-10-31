using App.Application.Abstractions;
using App.Application.Common;
using App.Application.Contracts;
using App.Domain.Entities;
using Microsoft.EntityFrameworkCore;

namespace App.Application.Features.Notifications;

/// <summary>
/// Реализация <see cref="INotificationsApi"/>: список уведомлений по автомобилю
/// и смена статуса прочтения. Данные — через <see cref="IAppDbContext"/>.
/// </summary>
internal sealed class NotificationsApi(
    IAppDbContext db,
    ICurrentUser currentUser) : INotificationsApi
{
    public async Task<NotificationPageDto> ListNotificationsAsync(
        Guid vehicleId,
        bool unreadOnly,
        int page,
        int pageSize,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(db);

        if (page < 1)
        {
            throw new BusinessRuleException("invalid_page", "Номер страницы должен быть не меньше 1.");
        }

        if (pageSize < 1 || pageSize > 100)
        {
            throw new BusinessRuleException("invalid_page_size", "Размер страницы должен быть от 1 до 100.");
        }

        var userId = currentUser.UserId ?? throw new UnauthorizedException("Требуется аутентификация.");

        var vehicle = await db.Vehicles.AsNoTracking()
            .FirstOrDefaultAsync(v => v.Id == vehicleId, cancellationToken);

        if (vehicle is null)
        {
            throw new NotFoundException("Автомобиль", vehicleId);
        }

        if (vehicle.DriverId != userId)
        {
            throw new ForbiddenException();
        }

        var query = db.Notifications.AsNoTracking()
            .Where(n => n.VehicleId == vehicleId);

        if (unreadOnly)
        {
            query = query.Where(n => n.ReadAt == null);
        }

        var total = await query.CountAsync(cancellationToken);

        var items = await query
            .OrderByDescending(n => n.CreatedAt)
            .Skip((page - 1) * pageSize)
            .Take(pageSize)
            .ToListAsync(cancellationToken);

        return new NotificationPageDto
        {
            Items = items.Select(ToDto).ToList(),
            Meta = new PageMetaDto
            {
                Page = page,
                PageSize = pageSize,
                Total = total,
            },
        };
    }

    public async Task MarkNotificationReadAsync(
        Guid notificationId,
        UpdateNotificationRequestDto body,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(body);

        var userId = currentUser.UserId ?? throw new UnauthorizedException("Требуется аутентификация.");

        var notification = await db.Notifications
            .FirstOrDefaultAsync(n => n.Id == notificationId, cancellationToken)
            ?? throw new NotFoundException("Уведомление", notificationId);

        if (notification.UserId != userId)
        {
            throw new ForbiddenException();
        }

        var isCurrentlyRead = notification.ReadAt.HasValue;

        if (body.Read && isCurrentlyRead)
        {
            return;
        }

        if (!body.Read && !isCurrentlyRead)
        {
            return;
        }

        notification.ReadAt = body.Read ? TimeProvider.System.GetUtcNow() : null;

        await db.SaveChangesAsync(cancellationToken);
    }

    private static NotificationDto ToDto(Notification notification) => new()
    {
        Id = notification.Id,
        VehicleId = notification.VehicleId,
        Kind = ContractEnums.Parse<NotificationKind>(notification.Kind),
        Severity = ContractEnums.Parse<NotificationSeverity>(notification.Severity),
        Title = notification.Title,
        Body = notification.Body,
        CreatedAt = notification.CreatedAt,
        ReadAt = notification.ReadAt,
        EventId = notification.EventId,
    };
}
