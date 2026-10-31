using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using App.Domain.Common;
using App.Domain.Entities;
using App.Infrastructure.Persistence;
using Microsoft.Extensions.DependencyInjection;
using NetTopologySuite.Geometries;

namespace App.IntegrationTests.Features;

/// <summary>Интеграционные тесты фичи Zones: список зон, поиск по координатам, карточка, ссылка на оплату.</summary>
public sealed class ZonesApiTests(AppFixture fixture)
{
    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    private static string Unique(string prefix) => prefix + Guid.NewGuid().ToString("N")[..8];

    private async Task<Guid> SeedZoneAsync(string displayName, string externalId, string city, double lat, double lon, short detectionRadiusM = 50, string entityState = "Active")
    {
        await using var scope = fixture.Factory.Services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();

        var zone = new ParkingZone
        {
            Id = Guid.NewGuid(),
            Provider = "test-provider",
            ExternalZoneId = externalId,
            City = city,
            DisplayName = displayName,
            Geometry = GeoConstants.CreatePoint(lat, lon),
            DetectionRadiusM = detectionRadiusM,
            BasePriceKopecks = 100,
            PriceDescription = "100 руб/час",
            MinTimeMinutes = 15,
            TimeStepMinutes = 15,
            HasHolidays = true,
            EntityState = entityState,
            LastSyncedAt = DateTimeOffset.UtcNow,
            CreatedAt = DateTimeOffset.UtcNow,
            UpdatedAt = DateTimeOffset.UtcNow
        };

        db.ParkingZones.Add(zone);
        await db.SaveChangesAsync(Ct);
        return zone.Id;
    }

    private async Task<Guid> SeedVehicleAsync(string plate, string vehicleType = "car")
    {
        await using var scope = fixture.Factory.Services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();

        var vehicle = new Vehicle
        {
            Id = Guid.NewGuid(),
            PlateNumber = plate,
            VehicleType = vehicleType,
            Model = "Test Model",
            IsActive = true,
            CreatedAt = DateTimeOffset.UtcNow,
            UpdatedAt = DateTimeOffset.UtcNow
        };

        db.Vehicles.Add(vehicle);
        await db.SaveChangesAsync(Ct);
        return vehicle.Id;
    }

    [Fact]
    public async Task ListZones_ValidBbox_ReturnsFeatureCollection()
    {
        var lat = 55 + Random.Shared.NextDouble() * 5;
        var lon = 30 + Random.Shared.NextDouble() * 5;
        var zoneId = await SeedZoneAsync(Unique("Zone"), Unique("EXT"), "SPB", lat, lon);

        using var client = fixture.CreateClient("admin");
        var bbox = $"{lon - 0.01},{lon + 0.01},{lat - 0.01},{lat + 0.01}";
        var response = await client.GetAsync($"/api/v1/zones?bbox={bbox}&limit=10", Ct);

        var text = await response.Content.ReadAsStringAsync(Ct);
        Assert.True(response.StatusCode == HttpStatusCode.OK, text);
        var body = JsonDocument.Parse(text).RootElement;
        Assert.Equal("FeatureCollection", body.GetProperty("type").GetString());
        Assert.True(body.GetProperty("features").GetArrayLength() >= 1);
    }

    [Fact]
    public async Task ListZones_InvalidBbox_Returns400WithCode()
    {
        using var client = fixture.CreateClient("admin");
        var response = await client.GetAsync("/api/v1/zones?bbox=1,2,3&limit=10", Ct);

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        var problem = await response.Content.ReadFromJsonAsync<JsonElement>(Ct);
        Assert.Equal("validation_failed", problem.GetProperty("code").GetString());
    }

    [Fact]
    public async Task ListZones_NonPositiveLimit_Returns400WithCode()
    {
        using var client = fixture.CreateClient("admin");
        var bbox = "30,31,55,56";
        var response = await client.GetAsync($"/api/v1/zones?bbox={bbox}&limit=0", Ct);

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        var problem = await response.Content.ReadFromJsonAsync<JsonElement>(Ct);
        Assert.Equal("validation_failed", problem.GetProperty("code").GetString());
    }

    [Fact]
    public async Task LookupZone_PointInsideZone_ReturnsZoneWithDistance()
    {
        var lat = 55 + Random.Shared.NextDouble() * 5;
        var lon = 30 + Random.Shared.NextDouble() * 5;
        var zoneId = await SeedZoneAsync(Unique("Zone"), Unique("EXT"), "SPB", lat, lon, detectionRadiusM: 100);

        using var client = fixture.CreateClient("admin");
        var response = await client.GetAsync($"/api/v1/zones/by-location?lat={lat}&lon={lon}", Ct);

        var text = await response.Content.ReadAsStringAsync(Ct);
        Assert.True(response.StatusCode == HttpStatusCode.OK, text);
        var body = JsonDocument.Parse(text).RootElement;
        Assert.Equal(zoneId.ToString(), body.GetProperty("zone").GetProperty("id").GetString());
        Assert.True(body.GetProperty("distanceM").ValueKind == JsonValueKind.Number);
    }

    [Fact]
    public async Task LookupZone_PointFarFromZone_ReturnsNullZone()
    {
        var lat = 55 + Random.Shared.NextDouble() * 5;
        var lon = 30 + Random.Shared.NextDouble() * 5;
        await SeedZoneAsync(Unique("Zone"), Unique("EXT"), "SPB", lat, lon, detectionRadiusM: 10);

        using var client = fixture.CreateClient("admin");
        var farLat = lat + 1;
        var farLon = lon + 1;
        var response = await client.GetAsync($"/api/v1/zones/by-location?lat={farLat}&lon={farLon}", Ct);

        var text = await response.Content.ReadAsStringAsync(Ct);
        Assert.True(response.StatusCode == HttpStatusCode.OK, text);
        var body = JsonDocument.Parse(text).RootElement;
        Assert.Null(body.GetProperty("zone").GetString());
    }

    [Fact]
    public async Task GetZone_ExistingZone_ReturnsDetails()
    {
        var lat = 55 + Random.Shared.NextDouble() * 5;
        var lon = 30 + Random.Shared.NextDouble() * 5;
        var zoneId = await SeedZoneAsync(Unique("Zone"), Unique("EXT"), "SPB", lat, lon);

        using var client = fixture.CreateClient("admin");
        var response = await client.GetAsync($"/api/v1/zones/{zoneId}", Ct);

        var text = await response.Content.ReadAsStringAsync(Ct);
        Assert.True(response.StatusCode == HttpStatusCode.OK, text);
        var body = JsonDocument.Parse(text).RootElement;
        Assert.Equal(zoneId.ToString(), body.GetProperty("id").GetString());
        Assert.True(body.GetProperty("geometry").ValueKind == JsonValueKind.Object);
        Assert.True(body.GetProperty("tariffPeriods").ValueKind == JsonValueKind.Array);
    }

    [Fact]
    public async Task GetZone_NonExistingZone_Returns404()
    {
        using var client = fixture.CreateClient("admin");
        var response = await client.GetAsync($"/api/v1/zones/{Guid.NewGuid()}", Ct);

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
    }

    [Fact]
    public async Task GetZonePaymentLink_ValidZoneAndVehicle_ReturnsLinks()
    {
        var lat = 55 + Random.Shared.NextDouble() * 5;
        var lon = 30 + Random.Shared.NextDouble() * 5;
        var zoneId = await SeedZoneAsync(Unique("Zone"), Unique("EXT"), "SPB", lat, lon);
        var plate = "А123ВС77";
        var vehicleId = await SeedVehicleAsync(plate);

        using var client = fixture.CreateClient("admin");
        var response = await client.GetAsync($"/api/v1/zones/{zoneId}/payment-link?vehicleId={vehicleId}", Ct);

        var text = await response.Content.ReadAsStringAsync(Ct);
        Assert.True(response.StatusCode == HttpStatusCode.OK, text);
        var body = JsonDocument.Parse(text).RootElement;
        Assert.Equal(zoneId.ToString(), body.GetProperty("zoneId").GetString());
        Assert.Contains(plate, body.GetProperty("webUrl").GetString());
        Assert.Contains(plate, body.GetProperty("appDeepLink").GetString());
    }

    [Fact]
    public async Task GetZonePaymentLink_NonExistingVehicle_Returns404()
    {
        var lat = 55 + Random.Shared.NextDouble() * 5;
        var lon = 30 + Random.Shared.NextDouble() * 5;
        var zoneId = await SeedZoneAsync(Unique("Zone"), Unique("EXT"), "SPB", lat, lon);

        using var client = fixture.CreateClient("admin");
        var response = await client.GetAsync($"/api/v1/zones/{zoneId}/payment-link?vehicleId={Guid.NewGuid()}", Ct);

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
    }
}
