using App.Application.Abstractions;
using App.Application.Common;
using App.Application.Contracts;
using App.Application.Integrations;
using App.Domain.Common;
using App.Domain.Entities;
using Microsoft.EntityFrameworkCore;
using System.Text.Json;

namespace App.Application.Features.Sessions;

/// <summary>
/// Реализация операций с парковочными сессиями.
/// </summary>
internal sealed class SessionsApi(
    IAppDbContext db,
    ICurrentUser currentUser,
    IOutbox outbox,
    IParkingPaymentProvider paymentProvider,
    IRequestContext requestContext,
    TimeProvider time) : ISessionsApi
{
    public async Task<ParkingSessionDto?> GetActiveSessionAsync(Guid vehicleId, CancellationToken cancellationToken)
    {
        var session = await db.ParkingSessions.AsNoTracking()
            .Include(s => s.Zone)
            .FirstOrDefaultAsync(s => s.VehicleId == vehicleId && s.Status == ContractEnums.ToValue(SessionStatus.Active), cancellationToken);

        if (session is null)
        {
            return null;
        }

        return ToDto(session, time.GetUtcNow());
    }

    public async Task<ParkingSessionPageDto> ListSessionsAsync(
        Guid? vehicleId,
        SessionStatus? status,
        DateTimeOffset? from,
        DateTimeOffset? to,
        int page,
        int pageSize,
        CancellationToken cancellationToken)
    {
        if (page < 1) page = 1;
        if (pageSize < 1) pageSize = 10;
        if (pageSize > 100) pageSize = 100;

        IQueryable<ParkingSession> query = db.ParkingSessions.AsNoTracking().Include(s => s.Zone);

        if (vehicleId.HasValue)
        {
            query = query.Where(s => s.VehicleId == vehicleId.Value);
        }

        if (status.HasValue)
        {
            var statusValue = ContractEnums.ToValue(status.Value);
            query = query.Where(s => s.Status == statusValue);
        }

        if (from.HasValue)
        {
            query = query.Where(s => s.StartedAt >= from.Value);
        }

        if (to.HasValue)
        {
            query = query.Where(s => s.StartedAt <= to.Value);
        }

        var total = await query.CountAsync(cancellationToken);

        var items = await query
            .OrderByDescending(s => s.StartedAt)
            .Skip((page - 1) * pageSize)
            .Take(pageSize)
            .ToListAsync(cancellationToken);

        var now = time.GetUtcNow();
        var dtos = items.Select(s => ToDto(s, now)).ToList();

        return new ParkingSessionPageDto
        {
            Items = dtos,
            Meta = new PageMetaDto
            {
                Page = page,
                PageSize = pageSize,
                Total = total
            }
        };
    }

    public async Task<ParkingSessionDto> StartSessionAsync(StartSessionRequestDto body, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(body);

        var vehicle = await db.Vehicles.AsNoTracking()
            .FirstOrDefaultAsync(v => v.Id == body.VehicleId, cancellationToken)
            ?? throw new NotFoundException("Автомобиль", body.VehicleId);

        var zone = await db.ParkingZones.AsNoTracking()
            .FirstOrDefaultAsync(z => z.Id == body.ZoneId, cancellationToken)
            ?? throw new NotFoundException("Зона", body.ZoneId);

        // Проверка наличия активной сессии
        var hasActive = await db.ParkingSessions.AsNoTracking()
            .AnyAsync(s => s.VehicleId == body.VehicleId && s.Status == ContractEnums.ToValue(SessionStatus.Active), cancellationToken);

        if (hasActive)
        {
            throw new ConflictException("active_session_exists", "У автомобиля уже есть активная парковочная сессия.");
        }

        // Проверка длительности
        if (body.DurationMinutes < zone.MinTimeMinutes)
        {
            throw new BusinessRuleException("duration_too_short", $"Длительность не может быть меньше {zone.MinTimeMinutes} минут.");
        }

        if (body.DurationMinutes % zone.TimeStepMinutes != 0)
        {
            throw new BusinessRuleException("duration_not_multiple", $"Длительность должна быть кратна {zone.TimeStepMinutes} минутам.");
        }

        var now = time.GetUtcNow();
        var idempotencyKey = requestContext.IdempotencyKey ?? Guid.NewGuid().ToString();

        // Оплата через провайдера
        var providerParking = await paymentProvider.StartAsync(
            zone.ExternalZoneId,
            vehicle.PlateNumber,
            vehicle.VehicleType,
            body.DurationMinutes,
            now,
            idempotencyKey,
            cancellationToken);

        var session = new ParkingSession
        {
            Id = Guid.NewGuid(),
            VehicleId = body.VehicleId,
            DriverId = vehicle.DriverId,
            ZoneId = body.ZoneId,
            Status = ContractEnums.ToValue(SessionStatus.Active),
            StartedAt = now,
            PaidUntil = providerParking.PaidUntil,
            EndedAt = null,
            CloseReason = null,
            DurationMinutes = (short)body.DurationMinutes,
            AmountKopecks = providerParking.AmountKopecks,
            PaymentProvider = "parkomatika",
            ExternalPaymentId = providerParking.ParkingId,
            ExpiryWarnedAt = null,
            IdempotencyKey = idempotencyKey,
            CreatedAt = now,
            UpdatedAt = now
        };

        db.ParkingSessions.Add(session);

        outbox.Enqueue(new SessionStarted(
            session.Id,
            session.VehicleId,
            session.ZoneId,
            session.StartedAt,
            session.PaidUntil,
            session.DurationMinutes,
            session.AmountKopecks,
            now));

        // Добавляем доменное событие в БД
        var domainEvent = new DomainEvent
        {
            Id = Guid.NewGuid(),
            EventType = "SessionStarted",
            EventVersion = 1,
            OccurredAt = now,
            VehicleId = session.VehicleId,
            SessionId = session.Id,
            ZoneId = session.ZoneId,
            Payload = JsonSerializer.Serialize(new
            {
                sessionId = session.Id,
                vehicleId = session.VehicleId,
                zoneId = session.ZoneId,
                startedAt = session.StartedAt,
                paidUntil = session.PaidUntil,
                durationMinutes = session.DurationMinutes,
                amountKopecks = session.AmountKopecks
            }),
            CreatedAt = now
        };
        db.DomainEvents.Add(domainEvent);

        await db.SaveChangesAsync(cancellationToken);

        // Загружаем зону для DTO
        var zoneForDto = await db.ParkingZones.AsNoTracking().FirstAsync(z => z.Id == zone.Id, cancellationToken);
        return ToDto(session, now, zoneForDto);
    }

    public async Task<ParkingSessionDto> GetSessionAsync(Guid sessionId, CancellationToken cancellationToken)
    {
        var session = await db.ParkingSessions.AsNoTracking()
            .Include(s => s.Zone)
            .FirstOrDefaultAsync(s => s.Id == sessionId, cancellationToken)
            ?? throw new NotFoundException("Сессия", sessionId);

        return ToDto(session, time.GetUtcNow());
    }

    public async Task<ParkingSessionDto> ExtendSessionAsync(Guid sessionId, ExtendSessionRequestDto body, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(body);

        var session = await db.ParkingSessions
            .Include(s => s.Zone)
            .FirstOrDefaultAsync(s => s.Id == sessionId, cancellationToken)
            ?? throw new NotFoundException("Сессия", sessionId);

        if (session.Status != ContractEnums.ToValue(SessionStatus.Active))
        {
            throw new ConflictException("session_not_active", "Сессия не находится в активном состоянии.");
        }

        if (body.AdditionalMinutes <= 0)
        {
            throw new BusinessRuleException("invalid_duration", "Количество минут должно быть положительным.");
        }

        var zone = session.Zone;
        if (body.AdditionalMinutes % zone.TimeStepMinutes != 0)
        {
            throw new BusinessRuleException("duration_not_multiple", $"Длительность должна быть кратна {zone.TimeStepMinutes} минутам.");
        }

        var idempotencyKey = requestContext.IdempotencyKey ?? Guid.NewGuid().ToString();

        var providerParking = await paymentProvider.ExtendAsync(
            session.ExternalPaymentId!,
            body.AdditionalMinutes,
            idempotencyKey,
            cancellationToken);

        var now = time.GetUtcNow();
        session.PaidUntil = providerParking.PaidUntil;
        session.AmountKopecks = providerParking.AmountKopecks;
        session.DurationMinutes = (short)(session.DurationMinutes + body.AdditionalMinutes);
        session.ExpiryWarnedAt = null;
        session.UpdatedAt = now;

        outbox.Enqueue(new SessionExtended(
            session.Id,
            session.VehicleId,
            body.AdditionalMinutes,
            session.PaidUntil,
            now));

        // Добавляем доменное событие в БД
        var domainEvent = new DomainEvent
        {
            Id = Guid.NewGuid(),
            EventType = "SessionExtended",
            EventVersion = 1,
            OccurredAt = now,
            VehicleId = session.VehicleId,
            SessionId = session.Id,
            ZoneId = session.ZoneId,
            Payload = JsonSerializer.Serialize(new
            {
                sessionId = session.Id,
                vehicleId = session.VehicleId,
                additionalMinutes = body.AdditionalMinutes,
                newPaidUntil = session.PaidUntil
            }),
            CreatedAt = now
        };
        db.DomainEvents.Add(domainEvent);

        await db.SaveChangesAsync(cancellationToken);

        return ToDto(session, now);
    }

    public async Task<ParkingSessionDto> CloseSessionAsync(Guid sessionId, CancellationToken cancellationToken)
    {
        var session = await db.ParkingSessions
            .Include(s => s.Zone)
            .FirstOrDefaultAsync(s => s.Id == sessionId, cancellationToken)
            ?? throw new NotFoundException("Сессия", sessionId);

        // Повторный вызов с тем же Idempotency-Key безопасен
        var idempotencyKey = requestContext.IdempotencyKey;
        if (idempotencyKey is not null && session.IdempotencyKey == idempotencyKey && session.Status != ContractEnums.ToValue(SessionStatus.Active))
        {
            return ToDto(session, time.GetUtcNow());
        }

        if (session.Status != ContractEnums.ToValue(SessionStatus.Active))
        {
            throw new ConflictException("session_not_active", "Сессия уже завершена.");
        }

        var now = time.GetUtcNow();

        // Завершение у провайдера
        await paymentProvider.StopAsync(session.ExternalPaymentId!, cancellationToken);

        session.Status = ContractEnums.ToValue(SessionStatus.Closed);
        session.EndedAt = now;
        session.CloseReason = ContractEnums.ToValue(SessionCloseReason.Manual);
        session.UpdatedAt = now;

        outbox.Enqueue(new SessionClosed(
            session.Id,
            session.VehicleId,
            session.ZoneId,
            session.StartedAt,
            now,
            SessionCloseReason.Manual,
            now));

        // Добавляем доменное событие в БД
        var domainEvent = new DomainEvent
        {
            Id = Guid.NewGuid(),
            EventType = "SessionClosed",
            EventVersion = 1,
            OccurredAt = now,
            VehicleId = session.VehicleId,
            SessionId = session.Id,
            ZoneId = session.ZoneId,
            Payload = JsonSerializer.Serialize(new
            {
                sessionId = session.Id,
                vehicleId = session.VehicleId,
                zoneId = session.ZoneId,
                startedAt = session.StartedAt,
                endedAt = now,
                closeReason = ContractEnums.ToValue(SessionCloseReason.Manual)
            }),
            CreatedAt = now
        };
        db.DomainEvents.Add(domainEvent);

        await db.SaveChangesAsync(cancellationToken);

        return ToDto(session, now);
    }

    private static ParkingSessionDto ToDto(ParkingSession session, DateTimeOffset now, ParkingZone? zoneOverride = null)
    {
        var zone = zoneOverride ?? session.Zone;
        var status = ContractEnums.Parse<SessionStatus>(session.Status);
        SessionCloseReason? closeReason = null;
        if (session.CloseReason is not null)
        {
            closeReason = ContractEnums.Parse<SessionCloseReason>(session.CloseReason);
        }

        int remainingSeconds = 0;
        if (status == SessionStatus.Active)
        {
            var diff = session.PaidUntil - now;
            remainingSeconds = Math.Max(0, (int)diff.TotalSeconds);
        }

        return new ParkingSessionDto
        {
            Id = session.Id,
            VehicleId = session.VehicleId,
            DriverId = session.DriverId,
            Zone = new ZoneSummaryDto
            {
                Id = zone.Id,
                ExternalZoneId = zone.ExternalZoneId,
                City = zone.City,
                DisplayName = zone.DisplayName,
                BasePriceKopecks = zone.BasePriceKopecks,
                PriceDescription = zone.PriceDescription,
                MinTimeMinutes = zone.MinTimeMinutes,
                TimeStepMinutes = zone.TimeStepMinutes
            },
            Status = status,
            StartedAt = session.StartedAt,
            PaidUntil = session.PaidUntil,
            EndedAt = session.EndedAt,
            CloseReason = closeReason,
            DurationMinutes = session.DurationMinutes,
            AmountKopecks = session.AmountKopecks,
            RemainingSeconds = remainingSeconds,
            ExternalPaymentId = session.ExternalPaymentId
        };
    }

    internal sealed record SessionStarted(
        Guid SessionId,
        Guid VehicleId,
        Guid ZoneId,
        DateTimeOffset StartedAt,
        DateTimeOffset PaidUntil,
        int DurationMinutes,
        int AmountKopecks,
        DateTimeOffset OccurredAt) : IIntegrationEvent
    {
        public string EventType => "SessionStarted";
        public int Version => 1;
        public string Topic => "sessions";
        public string PartitionKey => VehicleId.ToString();
    }

    internal sealed record SessionExtended(
        Guid SessionId,
        Guid VehicleId,
        int AdditionalMinutes,
        DateTimeOffset NewPaidUntil,
        DateTimeOffset OccurredAt) : IIntegrationEvent
    {
        public string EventType => "SessionExtended";
        public int Version => 1;
        public string Topic => "sessions";
        public string PartitionKey => VehicleId.ToString();
    }

    internal sealed record SessionClosed(
        Guid SessionId,
        Guid VehicleId,
        Guid ZoneId,
        DateTimeOffset StartedAt,
        DateTimeOffset EndedAt,
        SessionCloseReason CloseReason,
        DateTimeOffset OccurredAt) : IIntegrationEvent
    {
        public string EventType => "SessionClosed";
        public int Version => 1;
        public string Topic => "sessions";
        public string PartitionKey => VehicleId.ToString();
    }
}
