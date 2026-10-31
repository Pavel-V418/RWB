using System.Net;
using App.Application.Common;
using App.Application.Integrations;
using App.Infrastructure.External;

namespace App.Infrastructure.Integrations;

/// <summary>Адаптер платёжного API «Паркоматики» (на стенде — мок reference/case1/mocks/parkomatika).</summary>
internal sealed class ParkomatikaPaymentProvider(HttpClient http) : ExternalProviderClient(http, "Паркоматика"), IParkingPaymentProvider
{
    public Task<ProviderParking> StartAsync(string externalZoneId, string plateNumber, string vehicleType, int durationMinutes,
        DateTimeOffset startsAt, string idempotencyKey, CancellationToken cancellationToken) =>
        SendAsync<ProviderParking>(HttpMethod.Post, "api/v1/parkings",
            new { externalZoneId, plateNumber, vehicleType, durationMinutes, startsAt }, idempotencyKey, cancellationToken);

    public Task<ProviderParking> ExtendAsync(string parkingId, int minutes, string idempotencyKey, CancellationToken cancellationToken) =>
        SendAsync<ProviderParking>(HttpMethod.Post, $"api/v1/parkings/{Uri.EscapeDataString(parkingId)}/extensions",
            new { minutes }, idempotencyKey, cancellationToken);

    public async Task<ProviderParking> StopAsync(string parkingId, CancellationToken cancellationToken)
    {
        var path = $"api/v1/parkings/{Uri.EscapeDataString(parkingId)}";
        try
        {
            return await SendAsync<ProviderParking>(HttpMethod.Post, path + "/stop", new { }, null, cancellationToken);
        }
        catch (ConflictException)
        {
            // Остановка идемпотентна: парковка уже завершена (повтор после сбоя на нашей стороне) — возвращаем её состояние
            return await SendAsync<ProviderParking>(HttpMethod.Get, path, null, null, cancellationToken);
        }
    }

    protected override AppException MapError(HttpStatusCode status, string body) => status switch
    {
        HttpStatusCode.NotFound => new NotFoundException("Парковка у провайдера", "?"),
        HttpStatusCode.Conflict => new ConflictException("provider_parking_not_active", "Парковка у провайдера уже завершена."),
        _ => base.MapError(status, body),
    };
}
