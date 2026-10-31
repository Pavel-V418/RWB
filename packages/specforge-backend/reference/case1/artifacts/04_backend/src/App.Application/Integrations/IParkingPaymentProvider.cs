namespace App.Application.Integrations;

/// <summary>
/// Оплата парковки у внешнего провайдера («Паркоматика»). Любой сбой провайдера —
/// <see cref="Common.ExternalProviderException"/> (HTTP 502 нашего API); 404/409 провайдера — доменные исключения.
/// idempotencyKey передавай тот же, что пришёл в запросе к нашему API: повтор не создаст вторую оплату.
/// </summary>
public interface IParkingPaymentProvider
{
    /// <summary>Оплатить парковку: externalZoneId — номер зоны провайдера (parking_zones.external_zone_id).</summary>
    Task<ProviderParking> StartAsync(string externalZoneId, string plateNumber, string vehicleType, int durationMinutes,
        DateTimeOffset startsAt, string idempotencyKey, CancellationToken cancellationToken);

    /// <summary>Продлить оплату на minutes минут.</summary>
    Task<ProviderParking> ExtendAsync(string parkingId, int minutes, string idempotencyKey, CancellationToken cancellationToken);

    /// <summary>Завершить парковку досрочно. Идемпотентно: уже завершённая парковка возвращается как есть.</summary>
    Task<ProviderParking> StopAsync(string parkingId, CancellationToken cancellationToken);
}

/// <summary>
/// Парковка у провайдера: ParkingId сохраняется в parking_sessions.external_payment_id.
/// PaidUntil и AmountKopecks — ИТОГОВЫЕ значения после операции (сумма накопительная, не прибавляй её к старой).
/// </summary>
public sealed record ProviderParking(string ParkingId, DateTimeOffset PaidUntil, int AmountKopecks, string Status);
