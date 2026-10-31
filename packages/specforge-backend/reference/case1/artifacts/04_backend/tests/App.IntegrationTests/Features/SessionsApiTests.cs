using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using App.Application.Abstractions;
using App.Application.Integrations;
using App.Domain.Common;
using App.Domain.Entities;
using App.Infrastructure.Persistence;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;

namespace App.IntegrationTests.Features;

/// <summary>Интеграционные тесты фичи Sessions: HTTP через WebApplicationFactory, данные — в общей БД PostGIS.</summary>
public sealed class SessionsApiTests(AppFixture fixture)
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

    private async Task<Guid> SeedSessionAsync(Guid vehicleId, Guid zoneId, string status, DateTimeOffset startedAt, DateTimeOffset paidUntil, short durationMinutes, int amountKopecks, string? closeReason = null, DateTimeOffset? endedAt = null)
    {
        await using var scope = fixture.Factory.Services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        var now = DateTimeOffset.UtcNow;
        var session = new ParkingSession
        {
            Id = Guid.NewGuid(),
            VehicleId = vehicleId,
            ZoneId = zoneId,
            Status = status,
            StartedAt = startedAt,
            PaidUntil = paidUntil,
            EndedAt = endedAt,
            CloseReason = closeReason,
            DurationMinutes = durationMinutes,
            AmountKopecks = amountKopecks,
            PaymentProvider = "parkomatika",
            ExternalPaymentId = "ext-" + Guid.NewGuid().ToString("N")[..8],
            CreatedAt = now,
            UpdatedAt = now
        };
        db.ParkingSessions.Add(session);
        await db.SaveChangesAsync(Ct);
        return session.Id;
    }

    private sealed class FakeParkingPaymentProvider : IParkingPaymentProvider
    {
        public Task<ProviderParking> StartAsync(string externalZoneId, string plateNumber, string vehicleType, int durationMinutes, DateTimeOffset startsAt, string idempotencyKey, CancellationToken cancellationToken)
        {
            var paidUntil = startsAt.AddMinutes(durationMinutes);
            var amount = durationMinutes * 100;
            var parkingId = "park-" + Guid.NewGuid().ToString("N")[..8];
            return Task.FromResult(new ProviderParking(parkingId, paidUntil, amount, "active"));
        }

        public Task<ProviderParking> ExtendAsync(string parkingId, int minutes, string idempotencyKey, CancellationToken cancellationToken)
        {
            var paidUntil = DateTimeOffset.UtcNow.AddMinutes(minutes);
            var amount = minutes * 100;
            return Task.FromResult(new ProviderParking(parkingId, paidUntil, amount, "active"));
        }

        public Task<ProviderParking> StopAsync(string parkingId, CancellationToken cancellationToken)
        {
            return Task.FromResult(new ProviderParking(parkingId, DateTimeOffset.UtcNow, 0, "stopped"));
        }
    }

    [Fact]
    public async Task GetActiveSession_ReturnsSession_WhenActiveExists()
    {
        var login = Unique("drv.");
        var userId = await SeedUserAsync(login, "driver");
        var vehicleId = await SeedVehicleAsync(UniquePlate(), "car", userId);
        var zoneId = await SeedZoneAsync(Unique("Zone"), Unique("ext"), "Moscow", 55.75, 37.62);
        var now = DateTimeOffset.UtcNow;
        await SeedSessionAsync(vehicleId, zoneId, "active", now, now.AddHours(1), 60, 6000);

        using var client = fixture.CreateClient("driver", userId);

        var response = await client.GetAsync($"/api/v1/vehicles/{vehicleId}/active-session", Ct);

        var text = await response.Content.ReadAsStringAsync(Ct);
        Assert.True(response.StatusCode == HttpStatusCode.OK, text);
        var body = JsonDocument.Parse(text).RootElement;
        Assert.Equal("active", body.GetProperty("status").GetString());
        Assert.Equal(vehicleId, body.GetProperty("vehicleId").GetGuid());
    }

    [Fact]
    public async Task GetActiveSession_Returns204_WhenNoActiveSession()
    {
        var login = Unique("drv.");
        var userId = await SeedUserAsync(login, "driver");
        var vehicleId = await SeedVehicleAsync(UniquePlate(), "car", userId);

        using var client = fixture.CreateClient("driver", userId);

        var response = await client.GetAsync($"/api/v1/vehicles/{vehicleId}/active-session", Ct);

        Assert.Equal(HttpStatusCode.NoContent, response.StatusCode);
    }

    [Fact]
    public async Task StartSession_CreatesActiveSession_ForValidRequest()
    {
        var login = Unique("drv.");
        var userId = await SeedUserAsync(login, "driver");
        var vehicleId = await SeedVehicleAsync(UniquePlate(), "car", userId);
        var zoneId = await SeedZoneAsync(Unique("Zone"), Unique("ext"), "Moscow", 55.75, 37.62);

        using var client = fixture.CreateClient("driver", userId, s => s.AddSingleton<IParkingPaymentProvider>(new FakeParkingPaymentProvider()));

        var request = new HttpRequestMessage(HttpMethod.Post, "/api/v1/sessions");
        request.Headers.Add("Idempotency-Key", Guid.NewGuid().ToString());
        request.Content = JsonContent.Create(new { vehicleId, zoneId, durationMinutes = 30 });

        var response = await client.SendAsync(request, Ct);

        var text = await response.Content.ReadAsStringAsync(Ct);
        Assert.True(response.StatusCode == HttpStatusCode.Created, text);
        var body = JsonDocument.Parse(text).RootElement;
        Assert.Equal("active", body.GetProperty("status").GetString());
        Assert.Equal(30, body.GetProperty("durationMinutes").GetInt32());

        await using var scope = fixture.Factory.Services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        var session = await db.ParkingSessions.AsNoTracking().FirstOrDefaultAsync(s => s.VehicleId == vehicleId && s.Status == "active", Ct);
        Assert.NotNull(session);
        Assert.Equal(zoneId, session!.ZoneId);
    }

    [Fact]
    public async Task StartSession_Returns409_WhenActiveSessionExists()
    {
        var login = Unique("drv.");
        var userId = await SeedUserAsync(login, "driver");
        var vehicleId = await SeedVehicleAsync(UniquePlate(), "car", userId);
        var zoneId = await SeedZoneAsync(Unique("Zone"), Unique("ext"), "Moscow", 55.75, 37.62);
        var now = DateTimeOffset.UtcNow;
        await SeedSessionAsync(vehicleId, zoneId, "active", now, now.AddHours(1), 60, 6000);

        using var client = fixture.CreateClient("driver", userId, s => s.AddSingleton<IParkingPaymentProvider>(new FakeParkingPaymentProvider()));

        var request = new HttpRequestMessage(HttpMethod.Post, "/api/v1/sessions");
        request.Headers.Add("Idempotency-Key", Guid.NewGuid().ToString());
        request.Content = JsonContent.Create(new { vehicleId, zoneId, durationMinutes = 30 });

        var response = await client.SendAsync(request, Ct);

        Assert.Equal(HttpStatusCode.Conflict, response.StatusCode);
        var problem = await response.Content.ReadFromJsonAsync<JsonElement>(Ct);
        Assert.Equal("active_session_exists", problem.GetProperty("code").GetString());
    }

    [Fact]
    public async Task ExtendSession_UpdatesPaidUntil_ForActiveSession()
    {
        var login = Unique("drv.");
        var userId = await SeedUserAsync(login, "driver");
        var vehicleId = await SeedVehicleAsync(UniquePlate(), "car", userId);
        var zoneId = await SeedZoneAsync(Unique("Zone"), Unique("ext"), "Moscow", 55.75, 37.62);
        var now = DateTimeOffset.UtcNow;
        var sessionId = await SeedSessionAsync(vehicleId, zoneId, "active", now, now.AddHours(1), 60, 6000);

        using var client = fixture.CreateClient("driver", userId, s => s.AddSingleton<IParkingPaymentProvider>(new FakeParkingPaymentProvider()));

        var request = new HttpRequestMessage(HttpMethod.Post, $"/api/v1/sessions/{sessionId}/extensions");
        request.Headers.Add("Idempotency-Key", Guid.NewGuid().ToString());
        request.Content = JsonContent.Create(new { additionalMinutes = 30 });

        var response = await client.SendAsync(request, Ct);

        var text = await response.Content.ReadAsStringAsync(Ct);
        Assert.True(response.StatusCode == HttpStatusCode.OK, text);
        var body = JsonDocument.Parse(text).RootElement;
        Assert.Equal("active", body.GetProperty("status").GetString());
        Assert.Equal(90, body.GetProperty("durationMinutes").GetInt32());
    }

    [Fact]
    public async Task CloseSession_ClosesActiveSession_WithManualReason()
    {
        var login = Unique("drv.");
        var userId = await SeedUserAsync(login, "driver");
        var vehicleId = await SeedVehicleAsync(UniquePlate(), "car", userId);
        var zoneId = await SeedZoneAsync(Unique("Zone"), Unique("ext"), "Moscow", 55.75, 37.62);
        var now = DateTimeOffset.UtcNow;
        var sessionId = await SeedSessionAsync(vehicleId, zoneId, "active", now, now.AddHours(1), 60, 6000);

        using var client = fixture.CreateClient("driver", userId, s => s.AddSingleton<IParkingPaymentProvider>(new FakeParkingPaymentProvider()));

        var request = new HttpRequestMessage(HttpMethod.Post, $"/api/v1/sessions/{sessionId}/closure");
        request.Headers.Add("Idempotency-Key", Guid.NewGuid().ToString());

        var response = await client.SendAsync(request, Ct);

        var text = await response.Content.ReadAsStringAsync(Ct);
        Assert.True(response.StatusCode == HttpStatusCode.OK, text);
        var body = JsonDocument.Parse(text).RootElement;
        Assert.Equal("closed", body.GetProperty("status").GetString());
        Assert.Equal("manual", body.GetProperty("closeReason").GetString());

        await using var scope = fixture.Factory.Services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        var session = await db.ParkingSessions.AsNoTracking().FirstAsync(s => s.Id == sessionId, Ct);
        Assert.Equal("closed", session.Status);
        Assert.Equal("manual", session.CloseReason);
    }

    [Fact]
    public async Task GetSession_Returns404_WhenSessionNotFound()
    {
        var login = Unique("drv.");
        var userId = await SeedUserAsync(login, "driver");
        var missingId = Guid.NewGuid();

        using var client = fixture.CreateClient("driver", userId);

        var response = await client.GetAsync($"/api/v1/sessions/{missingId}", Ct);

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
        var problem = await response.Content.ReadFromJsonAsync<JsonElement>(Ct);
        Assert.Equal("not_found", problem.GetProperty("code").GetString());
    }
}
