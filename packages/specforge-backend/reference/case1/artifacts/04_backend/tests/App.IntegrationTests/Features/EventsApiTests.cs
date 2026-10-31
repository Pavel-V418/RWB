using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using App.Application.Abstractions;
using App.Domain.Common;
using App.Domain.Entities;
using App.Infrastructure.Persistence;
using Microsoft.Extensions.DependencyInjection;

namespace App.IntegrationTests.Features;

public sealed class EventsApiTests(AppFixture fixture)
{
    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    private static string Unique(string prefix) => prefix + Guid.NewGuid().ToString("N")[..8];

    private static string UniquePlate()
    {
        var letters = "АВЕКМНОРСТУХ";
        var r = Random.Shared;
        var first = letters[r.Next(letters.Length)];
        var d1 = r.Next(10).ToString();
        var d2 = r.Next(10).ToString();
        var d3 = r.Next(10).ToString();
        var l1 = letters[r.Next(letters.Length)];
        var l2 = letters[r.Next(letters.Length)];
        var region = r.Next(100, 999).ToString();
        return $"{first}{d1}{d2}{d3}{l1}{l2}{region}";
    }

    private async Task<Guid> SeedUserAsync(string login, string role, string password = "Secret-123!")
    {
        await using var scope = fixture.Factory.Services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        var hasher = scope.ServiceProvider.GetRequiredService<IPasswordHasher>();
        var user = new User
        {
            Id = Guid.NewGuid(),
            Login = login,
            PasswordHash = hasher.Hash(password),
            Role = role,
            FullName = "IT",
            CreatedAt = DateTimeOffset.UtcNow,
            UpdatedAt = DateTimeOffset.UtcNow
        };
        db.Users.Add(user);
        await db.SaveChangesAsync(Ct);
        return user.Id;
    }

    private async Task<Guid> SeedVehicleAsync(string plate, string type, Guid? driverId, string? model = null)
    {
        await using var scope = fixture.Factory.Services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        var now = DateTimeOffset.UtcNow;
        var vehicle = new Vehicle
        {
            Id = Guid.NewGuid(),
            PlateNumber = plate,
            VehicleType = type,
            Model = model,
            DriverId = driverId,
            IsActive = true,
            CreatedAt = now,
            UpdatedAt = now
        };
        db.Vehicles.Add(vehicle);
        await db.SaveChangesAsync(Ct);
        return vehicle.Id;
    }

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

    private async Task<Guid> SeedEventAsync(Guid vehicleId, string eventType, DateTimeOffset occurredAt, string payload = "{}")
    {
        await using var scope = fixture.Factory.Services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        var now = DateTimeOffset.UtcNow;
        var evt = new DomainEvent
        {
            Id = Guid.NewGuid(),
            EventType = eventType,
            EventVersion = 1,
            OccurredAt = occurredAt,
            VehicleId = vehicleId,
            Payload = payload,
            CreatedAt = now
        };
        db.DomainEvents.Add(evt);
        await db.SaveChangesAsync(Ct);
        return evt.Id;
    }

    [Fact]
    public async Task ListEvents_ReturnsPagedEvents_ForAdmin()
    {
        var login = Unique("admin.");
        await SeedUserAsync(login, "admin");
        using var client = fixture.CreateClient("admin");

        var vehicleId = await SeedVehicleAsync(UniquePlate(), "car", null);
        var now = DateTimeOffset.UtcNow;
        await SeedEventAsync(vehicleId, "ZoneEntered", now.AddMinutes(-2));
        await SeedEventAsync(vehicleId, "ZoneExited", now.AddMinutes(-1));

        var response = await client.GetAsync($"/api/v1/events?vehicleId={vehicleId}&page=1&pageSize=10", Ct);

        var text = await response.Content.ReadAsStringAsync(Ct);
        Assert.True(response.StatusCode == HttpStatusCode.OK, text);
        var body = JsonDocument.Parse(text).RootElement;
        Assert.Equal(2, body.GetProperty("meta").GetProperty("total").GetInt32());
        Assert.Equal(2, body.GetProperty("items").GetArrayLength());
        Assert.Equal("ZoneExited", body.GetProperty("items")[0].GetProperty("type").GetString());
        Assert.Equal("ZoneEntered", body.GetProperty("items")[1].GetProperty("type").GetString());
    }

    [Fact]
    public async Task ListEvents_Returns401_ForAnonymous()
    {
        using var client = fixture.CreateClient();

        var response = await client.GetAsync("/api/v1/events", Ct);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task ListEvents_Returns403_ForDriverRole()
    {
        var login = Unique("driver.");
        await SeedUserAsync(login, "driver");
        using var client = fixture.CreateClient("driver");

        var response = await client.GetAsync("/api/v1/events", Ct);

        Assert.Equal(HttpStatusCode.Forbidden, response.StatusCode);
    }

    [Fact]
    public async Task ListEvents_Returns400_ForInvalidPage()
    {
        var login = Unique("admin.");
        await SeedUserAsync(login, "admin");
        using var client = fixture.CreateClient("admin");

        var response = await client.GetAsync("/api/v1/events?page=0&pageSize=10", Ct);

        var text = await response.Content.ReadAsStringAsync(Ct);
        Assert.True(response.StatusCode == HttpStatusCode.BadRequest, text);
        var body = JsonDocument.Parse(text).RootElement;
        Assert.Equal("validation_failed", body.GetProperty("code").GetString());
    }

    [Fact]
    public async Task ListEvents_Returns422_ForInvalidPageSize()
    {
        var login = Unique("admin.");
        await SeedUserAsync(login, "admin");
        using var client = fixture.CreateClient("admin");

        var response = await client.GetAsync("/api/v1/events?page=1&pageSize=101", Ct);

        var text = await response.Content.ReadAsStringAsync(Ct);
        Assert.True(response.StatusCode == HttpStatusCode.UnprocessableEntity, text);
        var body = JsonDocument.Parse(text).RootElement;
        Assert.Equal("invalid_page_size", body.GetProperty("code").GetString());
    }

    [Fact]
    public async Task ListEvents_Returns422_ForInvalidDateRange()
    {
        var login = Unique("admin.");
        await SeedUserAsync(login, "admin");
        using var client = fixture.CreateClient("admin");

        var from = DateTimeOffset.UtcNow.AddHours(1);
        var to = DateTimeOffset.UtcNow;
        var response = await client.GetAsync($"/api/v1/events?from={Uri.EscapeDataString(from.ToString("O"))}&to={Uri.EscapeDataString(to.ToString("O"))}", Ct);

        var text = await response.Content.ReadAsStringAsync(Ct);
        Assert.True(response.StatusCode == HttpStatusCode.UnprocessableEntity, text);
        var body = JsonDocument.Parse(text).RootElement;
        Assert.Equal("invalid_date_range", body.GetProperty("code").GetString());
    }

    [Fact]
    public async Task ListEvents_FiltersByType_ForAdmin()
    {
        var login = Unique("admin.");
        await SeedUserAsync(login, "admin");
        using var client = fixture.CreateClient("admin");

        var vehicleId = await SeedVehicleAsync(UniquePlate(), "car", null);
        var now = DateTimeOffset.UtcNow;
        await SeedEventAsync(vehicleId, "ZoneEntered", now.AddMinutes(-2));
        await SeedEventAsync(vehicleId, "SessionStarted", now.AddMinutes(-1));

        var response = await client.GetAsync($"/api/v1/events?vehicleId={vehicleId}&type=ZoneEntered&page=1&pageSize=10", Ct);

        var text = await response.Content.ReadAsStringAsync(Ct);
        Assert.True(response.StatusCode == HttpStatusCode.OK, text);
        var body = JsonDocument.Parse(text).RootElement;
        Assert.Equal(1, body.GetProperty("meta").GetProperty("total").GetInt32());
        Assert.Equal("ZoneEntered", body.GetProperty("items")[0].GetProperty("type").GetString());
    }
}
