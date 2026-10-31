using System.Text.Json;
using App.Application.Abstractions;
using App.Application.Common;
using App.Application.Contracts;
using App.Application.Integrations;
using App.Domain.Common;
using App.Domain.Entities;
using Microsoft.EntityFrameworkCore;
using NetTopologySuite.Geometries;

namespace App.Application.Features.Tracking;

/// <summary>
/// Реализация <see cref="ITrackingApi"/>: приём GPS-точек, определение зоны,
/// переходы (вход/выход/смена), сессии парковки, уведомления и маршрут.
/// </summary>
internal sealed class TrackingApi(
    IAppDbContext db,
    IParkingPaymentProvider paymentProvider,
    ICurrentUser currentUser,
    IRequestContext requestContext,
    IOutbox outbox,
    TimeProvider time) : ITrackingApi
{
    public async Task<ReportPositionsResponseDto> ReportPositionsAsync(
        Guid vehicleId,
        ReportPositionsRequestDto body,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(body);
        if (body.Points.Count == 0)
        {
            throw new BusinessRuleException("empty_points", "Пакет точек пуст.");
        }

        var vehicle = await db.Vehicles.AsNoTracking()
            .SingleOrDefaultAsync(v => v.Id == vehicleId && v.IsActive, cancellationToken)
            ?? throw new NotFoundException("Vehicle", vehicleId);

        // Проверка AC-FR-6-02: координата из будущего отклоняется
        var serverNow = time.GetUtcNow();
        var futureTolerance = TimeSpan.FromMinutes(1);
        for (int i = 0; i < body.Points.Count; i++)
        {
            if (body.Points[i].RecordedAt > serverNow + futureTolerance)
            {
                throw new RequestValidationException($"Points[{i}].RecordedAt", "Время точки в будущем относительно сервера");
            }
        }

        // Сортируем по возрастанию recordedAt
        var sortedPoints = body.Points.OrderBy(p => p.RecordedAt).ToList();

        // Получаем текущее состояние зоны (с отслеживанием для последующего обновления)
        var zoneState = await db.VehicleZoneStates
            .Include(zs => zs.CurrentZone)
            .SingleOrDefaultAsync(zs => zs.VehicleId == vehicleId, cancellationToken);

        var currentZoneId = zoneState?.CurrentZoneId;
        var currentZone = currentZoneId.HasValue
            ? await db.ParkingZones.AsNoTracking().FirstOrDefaultAsync(z => z.Id == currentZoneId.Value, cancellationToken)
            : null;

        var accepted = 0;
        var duplicates = 0;
        var transition = ZoneTransition.None;
        var notifications = new List<NotificationDto>();
        var now = time.GetUtcNow();

        // Определяем, есть ли активная сессия (с отслеживанием изменений)
        var activeSession = await db.ParkingSessions
            .Include(s => s.Zone)
            .FirstOrDefaultAsync(s => s.VehicleId == vehicleId && s.Status == ContractEnums.ToValue(SessionStatus.Active), cancellationToken);

        // Список кандидатов: (zoneId, considered, recordedAt)
        var pointCandidates = new List<(Guid? ZoneId, bool Considered, DateTimeOffset RecordedAt)>();

        // Зона до последнего перехода в пакете (для previousZone в ответе)
        Guid? lastPreviousZoneId = null;
        ParkingZone? lastPreviousZone = null;

        // Обрабатываем каждую точку
        foreach (var point in sortedPoints)
        {
            // Идемпотентность: проверяем, есть ли уже точка с таким recordedAt
            var existing = await db.VehiclePositions.AsNoTracking()
                .AnyAsync(p => p.VehicleId == vehicleId && p.RecordedAt == point.RecordedAt, cancellationToken);

            if (existing)
            {
                duplicates++;
                continue;
            }

            // Определяем зону по точке
            Guid? pointZoneId = null;
            bool isConsidered = false;

            // Точка с accuracyM > 100 сохраняется с zone_id = null и не участвует в определении зоны
            if (point.AccuracyM.HasValue && point.AccuracyM.Value > 100)
            {
                pointZoneId = null;
                isConsidered = false;
            }
            else
            {
                // Кандидат — find_zone_at(lon, lat)
                var zoneAtPoint = await FindZoneAtAsync(point.Lon, point.Lat, cancellationToken);
                pointZoneId = zoneAtPoint?.Id;
                isConsidered = true;

                // Гистерезис: если у автомобиля есть текущая зона и точка находится в пределах её detection_radius_m,
                // кандидатом остаётся текущая зона, даже если find_zone_at вернул другую
                if (currentZoneId.HasValue && pointZoneId.HasValue && pointZoneId != currentZoneId.Value)
                {
                    var isInCurrentZoneRadius = await IsInZoneRadiusAsync(currentZoneId.Value, point.Lon, point.Lat, cancellationToken);
                    if (isInCurrentZoneRadius)
                    {
                        pointZoneId = currentZoneId.Value;
                    }
                }
            }

            // Сохраняем точку
            var position = new VehiclePosition
            {
                VehicleId = vehicleId,
                RecordedAt = point.RecordedAt,
                ReceivedAt = now,
                Location = GeoConstants.CreatePoint(point.Lat, point.Lon),
                AccuracyM = point.AccuracyM.HasValue ? (float)point.AccuracyM.Value : null,
                SpeedKmh = point.SpeedKmh.HasValue ? (float)point.SpeedKmh.Value : null,
                HeadingDeg = point.HeadingDeg.HasValue ? (float)point.HeadingDeg.Value : null,
                ZoneId = pointZoneId,
                Source = ContractEnums.ToValue(body.Source),
            };
            db.VehiclePositions.Add(position);
            accepted++;

            // Добавляем кандидата в список
            pointCandidates.Add((pointZoneId, isConsidered, point.RecordedAt));

            // Проверяем переход
            if (isConsidered)
            {
                var currentCandidate = pointZoneId;

                // Ищем предыдущую учитываемую точку в текущем пакете
                Guid? prevCandidate = null;
                DateTimeOffset? prevRecordedAt = null;
                bool foundPrevInPackage = false;
                for (int i = pointCandidates.Count - 2; i >= 0; i--)
                {
                    if (pointCandidates[i].Considered)
                    {
                        prevCandidate = pointCandidates[i].ZoneId;
                        prevRecordedAt = pointCandidates[i].RecordedAt;
                        foundPrevInPackage = true;
                        break;
                    }
                }

                // Если в пакете нет предыдущей учитываемой точки, берём из БД
                if (!foundPrevInPackage)
                {
                    var lastConsideredPosition = await db.VehiclePositions.AsNoTracking()
                        .Where(p => p.VehicleId == vehicleId 
                            && p.RecordedAt < point.RecordedAt 
                            && (p.AccuracyM == null || p.AccuracyM <= 100))
                        .OrderByDescending(p => p.RecordedAt)
                        .FirstOrDefaultAsync(cancellationToken);

                    if (lastConsideredPosition != null)
                    {
                        prevCandidate = lastConsideredPosition.ZoneId;
                        prevRecordedAt = lastConsideredPosition.RecordedAt;
                    }
                }

                // Проверяем условие перехода: текущий кандидат и предыдущий кандидат совпадают и отличаются от текущей зоны.
                // Предыдущий кандидат «вне зон» (null) — тоже кандидат; без предыдущей учитываемой точки перехода нет.
                if (prevRecordedAt.HasValue)
                {
                    bool shouldTransition = false;
                    if (currentCandidate == prevCandidate)
                    {
                        if (currentCandidate.HasValue)
                        {
                            // Вход или смена зоны
                            if (currentZoneId != currentCandidate.Value)
                            {
                                shouldTransition = true;
                            }
                        }
                        else
                        {
                            // Выход из зоны
                            if (currentZoneId.HasValue)
                            {
                                shouldTransition = true;
                            }
                        }
                    }

                    if (shouldTransition)
                    {
                        // Зона до перехода; при смене зоны через участок вне зон — покинутая зона A
                        var zoneBeforeTransition = currentZoneId;
                        var zoneBeforeTransitionEntity = currentZone;

                        // Определяем тип перехода
                        if (currentZoneId.HasValue && currentCandidate.HasValue)
                        {
                            transition = ZoneTransition.Changed;
                        }
                        else if (currentZoneId.HasValue && !currentCandidate.HasValue)
                        {
                            transition = ZoneTransition.Exited;
                        }
                        else if (!currentZoneId.HasValue && currentCandidate.HasValue)
                        {
                            // Проверяем, был ли это переход между соседними зонами
                            // Последняя точка в зоне до ПЕРВОЙ из двух подтверждающих точек: в пакете или в БД
                            var lastInZone = pointCandidates
                                .Where(c => c.Considered && c.ZoneId.HasValue && c.RecordedAt < prevRecordedAt.Value)
                                .Select(c => ((Guid ZoneId, DateTimeOffset RecordedAt)?)(c.ZoneId!.Value, c.RecordedAt))
                                .LastOrDefault()
                                ?? await GetLastZonePointBeforeAsync(vehicleId, prevRecordedAt.Value, cancellationToken);

                            // Переход между соседними зонами через короткий участок вне зон (≤ 300 с) — смена зоны
                            transition = lastInZone.HasValue && lastInZone.Value.ZoneId != currentCandidate.Value
                                && (prevRecordedAt.Value - lastInZone.Value.RecordedAt).TotalSeconds <= 300
                                ? ZoneTransition.Changed
                                : ZoneTransition.Entered;
                            if (transition == ZoneTransition.Changed)
                            {
                                zoneBeforeTransition = lastInZone!.Value.ZoneId;
                                zoneBeforeTransitionEntity = await db.ParkingZones.AsNoTracking()
                                    .FirstOrDefaultAsync(z => z.Id == zoneBeforeTransition.Value, cancellationToken);
                            }
                        }

                        // Обновляем текущую зону
                        currentZoneId = currentCandidate;
                        currentZone = currentZoneId.HasValue
                            ? await db.ParkingZones.AsNoTracking().FirstOrDefaultAsync(z => z.Id == currentZoneId.Value, cancellationToken)
                            : null;

                        // Запоминаем для previousZone в ответе
                        lastPreviousZoneId = zoneBeforeTransition;
                        lastPreviousZone = zoneBeforeTransitionEntity;

                        // Создаём запись в domain_events
                        var domainEvent = new DomainEvent
                        {
                            Id = Guid.NewGuid(),
                            EventType = transition switch
                            {
                                ZoneTransition.Entered => "ZoneEntered",
                                ZoneTransition.Exited => "ZoneExited",
                                ZoneTransition.Changed => "ZoneChanged",
                                _ => "ZoneEntered"
                            },
                            EventVersion = 1,
                            OccurredAt = point.RecordedAt,
                            VehicleId = vehicleId,
                            SessionId = activeSession?.Id,
                            ZoneId = transition == ZoneTransition.Exited ? zoneBeforeTransition : currentZoneId,
                            Payload = JsonSerializer.Serialize(new
                            {
                                FromZoneId = zoneBeforeTransition,
                                ToZoneId = currentZoneId
                            }),
                            CreatedAt = now,
                        };
                        db.DomainEvents.Add(domainEvent);

                        // Создаём уведомление
                        var notification = CreateTransitionNotification(vehicleId, transition, currentZone, zoneBeforeTransitionEntity, domainEvent.Id, now);
                        db.Notifications.Add(notification);
                        notifications.Add(ToNotificationDto(notification));

                        // Публикуем событие через outbox
                        if (transition == ZoneTransition.Entered)
                        {
                            outbox.Enqueue(new ZoneEntered(vehicleId, currentZoneId, point.RecordedAt));
                        }
                        else if (transition == ZoneTransition.Exited)
                        {
                            outbox.Enqueue(new ZoneExited(vehicleId, zoneBeforeTransition, point.RecordedAt));
                        }
                        else if (transition == ZoneTransition.Changed)
                        {
                            outbox.Enqueue(new ZoneChanged(vehicleId, zoneBeforeTransition, currentZoneId, point.RecordedAt));
                        }

                        // Если вышли из зоны и есть активная сессия — закрываем её
                        if (transition == ZoneTransition.Exited && activeSession != null)
                        {
                            await CloseSessionAsync(activeSession, SessionCloseReason.ZoneExit, now, cancellationToken);
                            activeSession = null;
                        }

                        // Если сменили зону и есть активную сессию — закрываем её
                        if (transition == ZoneTransition.Changed && activeSession != null)
                        {
                            await CloseSessionAsync(activeSession, SessionCloseReason.ZoneChanged, now, cancellationToken);
                            activeSession = null;
                        }
                    }
                }
            }
        }

        // Обновляем состояние зоны
        if (zoneState == null)
        {
            zoneState = new VehicleZoneState
            {
                VehicleId = vehicleId,
                CurrentZoneId = currentZoneId,
                EnteredAt = currentZoneId.HasValue ? sortedPoints.Last().RecordedAt : null,
                LastLocation = GeoConstants.CreatePoint(sortedPoints.Last().Lat, sortedPoints.Last().Lon),
                LastPositionAt = sortedPoints.Last().RecordedAt,
                UpdatedAt = now,
            };
            db.VehicleZoneStates.Add(zoneState);
        }
        else
        {
            var oldZoneId = zoneState.CurrentZoneId;
            zoneState.CurrentZoneId = currentZoneId;
            
            // Если зона изменилась, обновляем EnteredAt
            if (currentZoneId != oldZoneId)
            {
                zoneState.EnteredAt = currentZoneId.HasValue ? sortedPoints.Last().RecordedAt : null;
            }
            
            zoneState.LastLocation = GeoConstants.CreatePoint(sortedPoints.Last().Lat, sortedPoints.Last().Lon);
            zoneState.LastPositionAt = sortedPoints.Last().RecordedAt;
            zoneState.UpdatedAt = now;
        }

        await db.SaveChangesAsync(cancellationToken);

        // Получаем обновлённую активную сессию
        var finalActiveSession = activeSession != null
            ? await db.ParkingSessions.AsNoTracking()
                .Include(s => s.Zone)
                .FirstOrDefaultAsync(s => s.Id == activeSession.Id, cancellationToken)
            : null;

        return new ReportPositionsResponseDto
        {
            Accepted = accepted,
            Duplicates = duplicates,
            Transition = transition,
            PreviousZone = lastPreviousZone != null ? ToZoneSummaryDto(lastPreviousZone) : null,
            CurrentZone = currentZone != null ? ToZoneSummaryDto(currentZone) : null,
            ActiveSession = finalActiveSession != null ? ToParkingSessionDto(finalActiveSession, now) : null,
            Notifications = notifications.Count > 0 ? notifications : null,
        };
    }

    public async Task<RouteFeatureDto> GetVehicleRouteAsync(
        Guid vehicleId,
        DateTimeOffset? from,
        DateTimeOffset? to,
        CancellationToken cancellationToken)
    {
        var vehicle = await db.Vehicles.AsNoTracking()
            .SingleOrDefaultAsync(v => v.Id == vehicleId, cancellationToken)
            ?? throw new NotFoundException("Vehicle", vehicleId);

        var query = db.VehiclePositions.AsNoTracking()
            .Where(p => p.VehicleId == vehicleId);

        if (from.HasValue)
        {
            query = query.Where(p => p.RecordedAt >= from.Value);
        }
        if (to.HasValue)
        {
            query = query.Where(p => p.RecordedAt <= to.Value);
        }

        var positions = await query
            .OrderBy(p => p.RecordedAt)
            .Select(p => new RoutePointDto(p.Location.Y, p.Location.X, p.RecordedAt))
            .ToListAsync(cancellationToken);

        if (positions.Count == 0)
        {
            throw new NotFoundException("Маршрут", vehicleId);
        }

        // Строим LineString
        var coordinates = positions.Select(p => new Coordinate(p.Lon, p.Lat)).ToArray();
        var lineString = GeoConstants.Factory.CreateLineString(coordinates);

        var timestamps = positions.Select(p => p.RecordedAt).ToList();

        return new RouteFeatureDto
        {
            Geometry = lineString,
            Properties = new RouteFeaturePropertiesDto
            {
                VehicleId = vehicleId,
                PointCount = positions.Count,
                From = positions.First().RecordedAt,
                To = positions.Last().RecordedAt,
                Timestamps = timestamps,
            },
        };
    }

    private async Task<ParkingZone?> FindZoneAtAsync(double lon, double lat, CancellationToken cancellationToken)
    {
        // Используем SQL-функцию find_zone_at
        var results = await db.ParkingZones
            .FromSql($"SELECT * FROM parking_zones WHERE id = (SELECT zone_id FROM find_zone_at({lon}, {lat}) LIMIT 1)")
            .AsNoTracking()
            .FirstOrDefaultAsync(cancellationToken);

        return results;
    }

    private async Task<bool> IsInZoneRadiusAsync(Guid zoneId, double lon, double lat, CancellationToken cancellationToken)
    {
        // Проверяем, находится ли точка в пределах detection_radius_m зоны
        var result = await db.ParkingZones
            .FromSql($"SELECT * FROM parking_zones WHERE id = {zoneId} AND ST_DWithin(geometry::geography, ST_SetSRID(ST_MakePoint({lon}, {lat}), 4326)::geography, detection_radius_m)")
            .AsNoTracking()
            .AnyAsync(cancellationToken);

        return result;
    }

    private async Task<(Guid ZoneId, DateTimeOffset RecordedAt)?> GetLastZonePointBeforeAsync(
        Guid vehicleId, DateTimeOffset beforeTime, CancellationToken cancellationToken)
    {
        // Последняя учитываемая точка автомобиля в какой-либо зоне до beforeTime
        var lastPosition = await db.VehiclePositions.AsNoTracking()
            .Where(p => p.VehicleId == vehicleId && p.ZoneId != null && p.RecordedAt < beforeTime
                && (p.AccuracyM == null || p.AccuracyM <= 100))
            .OrderByDescending(p => p.RecordedAt)
            .FirstOrDefaultAsync(cancellationToken);

        return lastPosition is null ? null : (lastPosition.ZoneId!.Value, lastPosition.RecordedAt);
    }

    private async Task CloseSessionAsync(
        ParkingSession session,
        SessionCloseReason reason,
        DateTimeOffset now,
        CancellationToken cancellationToken)
    {
        session.Status = ContractEnums.ToValue(SessionStatus.Closed);
        session.EndedAt = now;
        session.CloseReason = ContractEnums.ToValue(reason);
        session.UpdatedAt = now;

        // Вызываем внешнего провайдера для завершения
        if (session.ExternalPaymentId != null)
        {
            try
            {
                await paymentProvider.StopAsync(session.ExternalPaymentId, cancellationToken);
            }
            catch (ExternalProviderException)
            {
                // Игнорируем ошибки провайдера при завершении, чтобы не ломать приём точек
            }
        }

        // Создаём событие
        var domainEvent = new DomainEvent
        {
            Id = Guid.NewGuid(),
            EventType = "SessionClosed",
            EventVersion = 1,
            OccurredAt = now,
            VehicleId = session.VehicleId,
            SessionId = session.Id,
            ZoneId = session.ZoneId,
            Payload = JsonSerializer.Serialize(new { SessionId = session.Id, Reason = reason.ToString() }),
            CreatedAt = now,
        };
        db.DomainEvents.Add(domainEvent);

        // Создаём уведомление
        var notification = new Notification
        {
            VehicleId = session.VehicleId,
            EventId = domainEvent.Id,
            Kind = ContractEnums.ToValue(NotificationKind.ZoneExited),
            Severity = ContractEnums.ToValue(NotificationSeverity.Info),
            Title = "Парковка завершена",
            Body = $"Парковка завершена: {reason.ToString().Replace('_', ' ')}.",
            CreatedAt = now,
        };
        db.Notifications.Add(notification);
    }

    private static Notification CreateTransitionNotification(
        Guid vehicleId,
        ZoneTransition transition,
        ParkingZone? zone,
        ParkingZone? previousZone,
        Guid eventId,
        DateTimeOffset now)
    {
        var kind = transition switch
        {
            ZoneTransition.Entered => NotificationKind.ZoneEntered,
            ZoneTransition.Exited => NotificationKind.ZoneExited,
            ZoneTransition.Changed => NotificationKind.ZoneChanged,
            _ => NotificationKind.ZoneEntered,
        };

        var title = transition switch
        {
            ZoneTransition.Entered => "Вход в зону",
            ZoneTransition.Exited => "Выход из зоны",
            ZoneTransition.Changed => "Смена зоны",
            _ => "Переход",
        };

        var body = transition switch
        {
            ZoneTransition.Entered => $"Вы въехали в платную зону {zone?.DisplayName ?? ""}",
            ZoneTransition.Exited => "Вы покинули текущую парковочную зону",
            ZoneTransition.Changed => "Вы въехали в другую парковочную зону. Проверьте необходимость новой оплаты",
            _ => "Переход между зонами.",
        };

        return new Notification
        {
            VehicleId = vehicleId,
            EventId = eventId,
            Kind = ContractEnums.ToValue(kind),
            Severity = ContractEnums.ToValue(NotificationSeverity.Info),
            Title = title,
            Body = body,
            CreatedAt = now,
        };
    }

    private static NotificationDto ToNotificationDto(Notification n) => new()
    {
        Id = n.Id,
        VehicleId = n.VehicleId,
        Kind = ContractEnums.Parse<NotificationKind>(n.Kind),
        Severity = ContractEnums.Parse<NotificationSeverity>(n.Severity),
        Title = n.Title,
        Body = n.Body,
        CreatedAt = n.CreatedAt,
        ReadAt = n.ReadAt,
        EventId = n.EventId,
    };

    private static ZoneSummaryDto ToZoneSummaryDto(ParkingZone zone) => new()
    {
        Id = zone.Id,
        ExternalZoneId = zone.ExternalZoneId,
        City = zone.City,
        DisplayName = zone.DisplayName,
        BasePriceKopecks = zone.BasePriceKopecks,
        PriceDescription = zone.PriceDescription,
        MinTimeMinutes = (int)zone.MinTimeMinutes,
        TimeStepMinutes = (int)zone.TimeStepMinutes,
    };

    private static ParkingSessionDto ToParkingSessionDto(ParkingSession session, DateTimeOffset now)
    {
        var remaining = Math.Max(0, (int)(session.PaidUntil - now).TotalSeconds);
        return new ParkingSessionDto
        {
            Id = session.Id,
            VehicleId = session.VehicleId,
            DriverId = session.DriverId,
            Zone = ToZoneSummaryDto(session.Zone),
            Status = ContractEnums.Parse<SessionStatus>(session.Status),
            StartedAt = session.StartedAt,
            PaidUntil = session.PaidUntil,
            EndedAt = session.EndedAt,
            CloseReason = session.CloseReason != null ? ContractEnums.Parse<SessionCloseReason>(session.CloseReason) : null,
            DurationMinutes = (int)session.DurationMinutes,
            AmountKopecks = session.AmountKopecks,
            RemainingSeconds = remaining,
            ExternalPaymentId = session.ExternalPaymentId,
        };
    }

    private sealed record RoutePointDto(double Lat, double Lon, DateTimeOffset RecordedAt);

    internal sealed record ZoneEntered(Guid VehicleId, Guid? ZoneId, DateTimeOffset OccurredAt) : IIntegrationEvent
    {
        public string EventType => "ZoneEntered";
        public int Version => 1;
        public string Topic => "tracking";
        public string PartitionKey => VehicleId.ToString();
    }

    internal sealed record ZoneExited(Guid VehicleId, Guid? ZoneId, DateTimeOffset OccurredAt) : IIntegrationEvent
    {
        public string EventType => "ZoneExited";
        public int Version => 1;
        public string Topic => "tracking";
        public string PartitionKey => VehicleId.ToString();
    }

    internal sealed record ZoneChanged(Guid VehicleId, Guid? FromZoneId, Guid? ToZoneId, DateTimeOffset OccurredAt) : IIntegrationEvent
    {
        public string EventType => "ZoneChanged";
        public int Version => 1;
        public string Topic => "tracking";
        public string PartitionKey => VehicleId.ToString();
    }
}
