using App.Application.Abstractions;
using App.Application.Common;
using App.Application.Contracts;
using App.Domain.Common;
using App.Domain.Entities;
using Microsoft.EntityFrameworkCore;
using NetTopologySuite.Geometries;

namespace App.Application.Features.Zones;

/// <summary>
/// Реализация операций тега Zones: список зон в bbox, поиск по координатам,
/// карточка зоны и ссылка на внешний сервис оплаты.
/// </summary>
internal sealed class ZonesApi(
    IAppDbContext db,
    TimeProvider time) : IZonesApi
{
    public async Task<ZoneFeatureCollectionDto> ListZonesAsync(string bbox, string? city, int limit, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(bbox);
        if (limit <= 0)
        {
            throw new BusinessRuleException("invalid_limit", "Лимит должен быть положительным.");
        }

        var (minLon, maxLon, minLat, maxLat) = ParseBbox(bbox);
        var envelope = GeoConstants.Factory.ToGeometry(new Envelope(minLon, maxLon, minLat, maxLat));

        var query = db.ParkingZones.AsNoTracking()
            .Where(z => z.EntityState == "Active" && z.Geometry.Intersects(envelope));

        if (!string.IsNullOrWhiteSpace(city))
        {
            query = query.Where(z => z.City == city);
        }

        var total = await query.CountAsync(cancellationToken);
        var zones = await query
            .OrderBy(z => z.DisplayName)
            .Skip(0)
            .Take(limit)
            .ToListAsync(cancellationToken);

        return new ZoneFeatureCollectionDto
        {
            Features = zones.Select(ToFeature).ToList(),
            Truncated = total > limit,
        };
    }

    public async Task<ZoneLookupResultDto> LookupZoneAsync(double lat, double lon, CancellationToken cancellationToken)
    {
        var point = GeoConstants.CreatePoint(lat, lon);

        // Используем готовую SQL-функцию find_zone_at для поиска ближайшей зоны
        var results = await db.ParkingZones
            .FromSql($"SELECT * FROM parking_zones WHERE id IN (SELECT zone_id FROM find_zone_at({lon}, {lat}))")
            .AsNoTracking()
            .Where(z => z.EntityState == "Active")
            .ToListAsync(cancellationToken);

        if (results.Count == 0)
        {
            return new ZoneLookupResultDto
            {
                Point = new GeoPointDto { Lat = lat, Lon = lon },
                Zone = null,
                DistanceM = null,
            };
        }

        // Находим ближайшую зону по расстоянию до геометрии
        var nearestZone = results
            .Select(z => new { Zone = z, Distance = point.Distance(z.Geometry) })
            .OrderBy(x => x.Distance)
            .First();

        // Проверяем, что расстояние не превышает detectionRadiusM
        if (nearestZone.Distance > nearestZone.Zone.DetectionRadiusM)
        {
            return new ZoneLookupResultDto
            {
                Point = new GeoPointDto { Lat = lat, Lon = lon },
                Zone = null,
                DistanceM = null,
            };
        }

        return new ZoneLookupResultDto
        {
            Point = new GeoPointDto { Lat = lat, Lon = lon },
            Zone = ToSummary(nearestZone.Zone),
            DistanceM = nearestZone.Distance,
        };
    }

    public async Task<ZoneDetailsDto> GetZoneAsync(Guid zoneId, CancellationToken cancellationToken)
    {
        var zone = await db.ParkingZones.AsNoTracking()
            .Include(z => z.ZoneTariffPeriods)
            .SingleOrDefaultAsync(z => z.Id == zoneId, cancellationToken)
            ?? throw new NotFoundException("Зона", zoneId);

        var now = time.GetUtcNow();
        var isPaidNow = IsPaidNowForCar(zone, now);

        return new ZoneDetailsDto
        {
            Id = zone.Id,
            ExternalZoneId = zone.ExternalZoneId,
            City = zone.City,
            DisplayName = zone.DisplayName,
            BasePriceKopecks = zone.BasePriceKopecks,
            PriceDescription = zone.PriceDescription,
            MinTimeMinutes = zone.MinTimeMinutes,
            TimeStepMinutes = zone.TimeStepMinutes,
            Geometry = zone.Geometry,
            TariffPeriods = zone.ZoneTariffPeriods.Select(ToTariffPeriodDto).ToList(),
            HasHolidays = zone.HasHolidays,
            DetectionRadiusM = zone.DetectionRadiusM,
            IsPaidNow = isPaidNow,
            LastSyncedAt = zone.LastSyncedAt,
        };
    }

    public async Task<PaymentLinkDto> GetZonePaymentLinkAsync(Guid zoneId, Guid vehicleId, CancellationToken cancellationToken)
    {
        var zone = await db.ParkingZones.AsNoTracking()
            .SingleOrDefaultAsync(z => z.Id == zoneId, cancellationToken)
            ?? throw new NotFoundException("Зона", zoneId);

        var vehicle = await db.Vehicles.AsNoTracking()
            .SingleOrDefaultAsync(v => v.Id == vehicleId, cancellationToken)
            ?? throw new NotFoundException("Транспортное средство", vehicleId);

        // Формируем ссылку на официальный сервис оплаты
        // Используем externalZoneId и номер автомобиля
        var webUrl = $"https://parking.spb.ru/pay?zone={zone.ExternalZoneId}&plate={vehicle.PlateNumber}";
        var appDeepLink = $"spbparking://pay?zone={zone.ExternalZoneId}&plate={vehicle.PlateNumber}";

        return new PaymentLinkDto
        {
            ZoneId = zone.Id,
            ExternalZoneId = zone.ExternalZoneId,
            WebUrl = webUrl,
            AppDeepLink = appDeepLink,
        };
    }

    private static (double MinLon, double MaxLon, double MinLat, double MaxLat) ParseBbox(string bbox)
    {
        var parts = bbox.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        if (parts.Length != 4)
        {
            throw new BusinessRuleException("invalid_bbox", "Параметр bbox должен содержать 4 значения: minLon, maxLon, minLat, maxLat.");
        }

        if (!double.TryParse(parts[0], out var minLon) ||
            !double.TryParse(parts[1], out var maxLon) ||
            !double.TryParse(parts[2], out var minLat) ||
            !double.TryParse(parts[3], out var maxLat))
        {
            throw new BusinessRuleException("invalid_bbox", "Некорректные значения bbox.");
        }

        if (minLon > maxLon || minLat > maxLat)
        {
            throw new BusinessRuleException("invalid_bbox", "Некорректный диапазон bbox.");
        }

        return (minLon, maxLon, minLat, maxLat);
    }

    private static bool IsPaidNowForCar(ParkingZone zone, DateTimeOffset now)
    {
        var currentMinute = now.Hour * 60 + now.Minute;
        var dayOfWeek = now.DayOfWeek;

        // Если есть праздничные дни и сегодня праздник — парковка бесплатна
        // (упрощённая проверка: считаем, что HasHolidays означает, что в праздники бесплатно)
        // Для точной проверки праздников нужна дополнительная логика, здесь упрощаем

        var carPeriods = zone.ZoneTariffPeriods
            .Where(t => t.VehicleType == "car")
            .ToList();

        if (carPeriods.Count == 0)
        {
            return false;
        }

        foreach (var period in carPeriods)
        {
            if (period.FromMinute is null || period.ToMinute is null)
            {
                // Круглосуточно
                return true;
            }

            var from = period.FromMinute.Value;
            var to = period.ToMinute.Value;

            if (from <= to)
            {
                if (currentMinute >= from && currentMinute < to)
                {
                    return true;
                }
            }
            else
            {
                // Период пересекает полночь
                if (currentMinute >= from || currentMinute < to)
                {
                    return true;
                }
            }
        }

        return false;
    }

    private static ZoneFeatureDto ToFeature(ParkingZone zone) => new()
    {
        Id = zone.Id,
        Geometry = zone.Geometry,
        Properties = ToSummary(zone),
    };

    private static ZoneSummaryDto ToSummary(ParkingZone zone) => new()
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

    private static TariffPeriodDto ToTariffPeriodDto(ZoneTariffPeriod period) => new()
    {
        VehicleType = ContractEnums.Parse<VehicleType>(period.VehicleType),
        FromMinute = period.FromMinute,
        ToMinute = period.ToMinute,
        DurationMinutes = period.DurationMinutes,
        PriceKopecks = period.PriceKopecks,
    };
}
