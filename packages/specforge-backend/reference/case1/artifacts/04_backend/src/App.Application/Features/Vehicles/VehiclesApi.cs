using App.Application.Abstractions;
using App.Application.Common;
using App.Application.Contracts;
using App.Domain.Entities;
using Microsoft.EntityFrameworkCore;

namespace App.Application.Features.Vehicles;

/// <summary>
/// Реализация <see cref="IVehiclesApi"/>: список/создание/статус автомобилей.
/// </summary>
internal sealed class VehiclesApi(
    IAppDbContext db,
    ICurrentUser currentUser,
    TimeProvider time) : IVehiclesApi
{
    public async Task<VehiclePageDto> ListVehiclesAsync(int page, int pageSize, CancellationToken cancellationToken)
    {
        if (page < 1) page = 1;
        if (pageSize < 1) pageSize = 10;
        if (pageSize > 200) pageSize = 200; // maximum из контракта

        var userId = currentUser.UserId ?? throw new UnauthorizedException("Требуется аутентификация.");
        var isAdmin = currentUser.IsInRole(Roles.Admin);

        IQueryable<Vehicle> query = db.Vehicles.AsNoTracking();

        if (!isAdmin)
        {
            query = query.Where(v => v.DriverId == userId);
        }

        var total = await query.CountAsync(cancellationToken);

        var items = await query
            .OrderBy(v => v.PlateNumber)
            .Skip((page - 1) * pageSize)
            .Take(pageSize)
            .ToListAsync(cancellationToken);

        return new VehiclePageDto
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

    public async Task<VehicleDto> CreateVehicleAsync(CreateVehicleRequestDto body, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(body);

        var userId = currentUser.UserId ?? throw new UnauthorizedException("Требуется аутентификация.");
        if (!currentUser.IsInRole(Roles.Admin))
        {
            throw new ForbiddenException();
        }

        // Проверка уникальности госномера
        var plateExists = await db.Vehicles.AsNoTracking()
            .AnyAsync(v => v.PlateNumber == body.PlateNumber, cancellationToken);
        if (plateExists)
        {
            throw new ConflictException("plate_exists", "Автомобиль с таким госномером уже зарегистрирован.");
        }

        // Если указан DriverId — проверяем существование
        if (body.DriverId is not null)
        {
            var driverExists = await db.Users.AsNoTracking()
                .AnyAsync(u => u.Id == body.DriverId.Value, cancellationToken);
            if (!driverExists)
            {
                throw new NotFoundException("Водитель", body.DriverId.Value);
            }
        }

        var now = time.GetUtcNow();
        var vehicle = new Vehicle
        {
            Id = Guid.NewGuid(),
            PlateNumber = body.PlateNumber,
            VehicleType = ContractEnums.ToValue(body.VehicleType),
            Model = body.Model,
            DriverId = body.DriverId,
            IsActive = true,
            CreatedAt = now,
            UpdatedAt = now,
        };

        db.Vehicles.Add(vehicle);
        await db.SaveChangesAsync(cancellationToken);

        return ToDto(vehicle);
    }

    public async Task<VehicleStatusDto> GetVehicleAsync(Guid vehicleId, CancellationToken cancellationToken)
    {
        var userId = currentUser.UserId ?? throw new UnauthorizedException("Требуется аутентификация.");
        var isAdmin = currentUser.IsInRole(Roles.Admin);

        var vehicle = await db.Vehicles.AsNoTracking()
            .Include(v => v.Driver)
            .SingleOrDefaultAsync(v => v.Id == vehicleId, cancellationToken)
            ?? throw new NotFoundException("Автомобиль", vehicleId);

        // Проверка доступа: admin видит все, driver — только свои
        if (!isAdmin && vehicle.DriverId != userId)
        {
            throw new ForbiddenException();
        }

        // Текущая зона
        var zoneState = await db.VehicleZoneStates.AsNoTracking()
            .Include(z => z.CurrentZone)
            .FirstOrDefaultAsync(z => z.VehicleId == vehicleId, cancellationToken);

        ZoneSummaryDto? currentZone = null;
        DateTimeOffset? enteredZoneAt = null;

        if (zoneState?.CurrentZone is not null)
        {
            currentZone = ToZoneSummary(zoneState.CurrentZone);
            enteredZoneAt = zoneState.EnteredAt;
        }

        // Последняя точка
        var lastPosition = await db.VehiclePositions.AsNoTracking()
            .Where(p => p.VehicleId == vehicleId)
            .OrderByDescending(p => p.RecordedAt)
            .FirstOrDefaultAsync(cancellationToken);

        PositionPointDto? lastPositionDto = null;
        if (lastPosition is not null)
        {
            lastPositionDto = new PositionPointDto
            {
                Lat = lastPosition.Location.Y,
                Lon = lastPosition.Location.X,
                RecordedAt = lastPosition.RecordedAt,
                AccuracyM = lastPosition.AccuracyM,
                SpeedKmh = lastPosition.SpeedKmh,
                HeadingDeg = lastPosition.HeadingDeg,
            };
        }

        // Активная сессия
        var activeSession = await db.ParkingSessions.AsNoTracking()
            .Include(s => s.Zone)
            .FirstOrDefaultAsync(s => s.VehicleId == vehicleId && s.Status == ContractEnums.ToValue(SessionStatus.Active), cancellationToken);

        ParkingSessionDto? activeSessionDto = null;
        if (activeSession is not null)
        {
            var now = time.GetUtcNow();
            var remainingSeconds = Math.Max(0, (int)(activeSession.PaidUntil - now).TotalSeconds);

            activeSessionDto = new ParkingSessionDto
            {
                Id = activeSession.Id,
                VehicleId = activeSession.VehicleId,
                DriverId = activeSession.DriverId,
                Zone = ToZoneSummary(activeSession.Zone),
                Status = ContractEnums.Parse<SessionStatus>(activeSession.Status),
                StartedAt = activeSession.StartedAt,
                PaidUntil = activeSession.PaidUntil,
                EndedAt = activeSession.EndedAt,
                CloseReason = activeSession.CloseReason is not null
                    ? ContractEnums.Parse<SessionCloseReason>(activeSession.CloseReason)
                    : null,
                DurationMinutes = activeSession.DurationMinutes,
                AmountKopecks = activeSession.AmountKopecks,
                RemainingSeconds = remainingSeconds,
                ExternalPaymentId = activeSession.ExternalPaymentId,
            };
        }

        return new VehicleStatusDto
        {
            Id = vehicle.Id,
            PlateNumber = vehicle.PlateNumber,
            VehicleType = ContractEnums.Parse<VehicleType>(vehicle.VehicleType),
            Model = vehicle.Model,
            DriverId = vehicle.DriverId,
            IsActive = vehicle.IsActive,
            CurrentZone = currentZone,
            EnteredZoneAt = enteredZoneAt,
            LastPosition = lastPositionDto,
            ActiveSession = activeSessionDto,
        };
    }

    private static VehicleDto ToDto(Vehicle vehicle) => new()
    {
        Id = vehicle.Id,
        PlateNumber = vehicle.PlateNumber,
        VehicleType = ContractEnums.Parse<VehicleType>(vehicle.VehicleType),
        Model = vehicle.Model,
        DriverId = vehicle.DriverId,
        IsActive = vehicle.IsActive,
    };

    private static ZoneSummaryDto ToZoneSummary(ParkingZone zone) => new()
    {
        Id = zone.Id,
        ExternalZoneId = zone.ExternalZoneId,
        City = zone.City,
        DisplayName = zone.DisplayName,
        BasePriceKopecks = zone.BasePriceKopecks,
        PriceDescription = zone.PriceDescription,
        MinTimeMinutes = zone.MinTimeMinutes,
        TimeStepMinutes = zone.TimeStepMinutes,
    };
}
