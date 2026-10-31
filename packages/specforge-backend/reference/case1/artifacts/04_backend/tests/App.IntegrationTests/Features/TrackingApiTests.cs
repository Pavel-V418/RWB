using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using App.Application.Abstractions;
using App.Domain.Common;
using App.Domain.Entities;
using App.Infrastructure.Persistence;
using Microsoft.Extensions.DependencyInjection;

namespace App.IntegrationTests.Features;

public sealed class TrackingApiTests(AppFixture fixture)
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

    [Fact]
    public async Task ReportPositions_ValidPoints_Returns200WithAcceptedCount()
    {
        var login = Unique("driver.");
        var userId = await SeedUserAsync(login, "driver");
        var vehicleId = await SeedVehicleAsync(UniquePlate(), "car", userId);
        using var client = fixture.CreateClient("driver", userId);

        var lat = 55 + Random.Shared.NextDouble() * 5;
        var lon = 30 + Random.Shared.NextDouble() * 5;
        var recordedAt = DateTimeOffset.UtcNow.AddMinutes(-1);

        var response = await client.PostAsJsonAsync(
            $"/api/v1/vehicles/{vehicleId}/positions",
            new
            {
                source = "gps",
                points = new[]
                {
                    new { lat, lon, recordedAt, accuracyM = 10.0, speedKmh = 50.0, headingDeg = 90.0 }
                }
            },
            Ct);

        var text = await response.Content.ReadAsStringAsync(Ct);
        Assert.True(response.StatusCode == HttpStatusCode.OK, text);
        var body = JsonDocument.Parse(text).RootElement;
        Assert.Equal(1, body.GetProperty("accepted").GetInt32());
        Assert.Equal(0, body.GetProperty("duplicates").GetInt32());
        Assert.Equal("none", body.GetProperty("transition").GetString());
    }

    [Fact]
    public async Task ReportPositions_EmptyPoints_Returns400WithValidationFailed()
    {
        var login = Unique("driver.");
        var userId = await SeedUserAsync(login, "driver");
        var vehicleId = await SeedVehicleAsync(UniquePlate(), "car", userId);
        using var client = fixture.CreateClient("driver", userId);

        var response = await client.PostAsJsonAsync(
            $"/api/v1/vehicles/{vehicleId}/positions",
            new
            {
                source = "gps",
                points = Array.Empty<object>()
            },
            Ct);

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        var problem = await response.Content.ReadFromJsonAsync<JsonElement>(Ct);
        Assert.Equal("validation_failed", problem.GetProperty("code").GetString());
    }

    [Fact]
    public async Task ReportPositions_FutureTime_Returns400WithValidationFailed()
    {
        var login = Unique("driver.");
        var userId = await SeedUserAsync(login, "driver");
        var vehicleId = await SeedVehicleAsync(UniquePlate(), "car", userId);
        using var client = fixture.CreateClient("driver", userId);

        var lat = 55 + Random.Shared.NextDouble() * 5;
        var lon = 30 + Random.Shared.NextDouble() * 5;
        var futureTime = DateTimeOffset.UtcNow.AddMinutes(5);

        var response = await client.PostAsJsonAsync(
            $"/api/v1/vehicles/{vehicleId}/positions",
            new
            {
                source = "gps",
                points = new[]
                {
                    new { lat, lon, recordedAt = futureTime, accuracyM = 10.0 }
                }
            },
            Ct);

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        var problem = await response.Content.ReadFromJsonAsync<JsonElement>(Ct);
        Assert.Equal("validation_failed", problem.GetProperty("code").GetString());
    }

    [Fact]
    public async Task ReportPositions_UnknownVehicle_Returns404()
    {
        var login = Unique("driver.");
        var userId = await SeedUserAsync(login, "driver");
        var vehicleId = Guid.NewGuid();
        using var client = fixture.CreateClient("driver", userId);

        var lat = 55 + Random.Shared.NextDouble() * 5;
        var lon = 30 + Random.Shared.NextDouble() * 5;
        var recordedAt = DateTimeOffset.UtcNow.AddMinutes(-1);

        var response = await client.PostAsJsonAsync(
            $"/api/v1/vehicles/{vehicleId}/positions",
            new
            {
                source = "gps",
                points = new[]
                {
                    new { lat, lon, recordedAt, accuracyM = 10.0 }
                }
            },
            Ct);

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
    }

    [Fact]
    public async Task ReportPositions_Anonymous_Returns401()
    {
        var login = Unique("driver.");
        var userId = await SeedUserAsync(login, "driver");
        var vehicleId = await SeedVehicleAsync(UniquePlate(), "car", userId);
        using var client = fixture.CreateClient();

        var lat = 55 + Random.Shared.NextDouble() * 5;
        var lon = 30 + Random.Shared.NextDouble() * 5;
        var recordedAt = DateTimeOffset.UtcNow.AddMinutes(-1);

        var response = await client.PostAsJsonAsync(
            $"/api/v1/vehicles/{vehicleId}/positions",
            new
            {
                source = "gps",
                points = new[]
                {
                    new { lat, lon, recordedAt, accuracyM = 10.0 }
                }
            },
            Ct);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task GetVehicleRoute_WithPositions_Returns200WithLineString()
    {
        var login = Unique("driver.");
        var userId = await SeedUserAsync(login, "driver");
        var vehicleId = await SeedVehicleAsync(UniquePlate(), "car", userId);
        using var client = fixture.CreateClient("driver", userId);

        var lat1 = 55 + Random.Shared.NextDouble() * 5;
        var lon1 = 30 + Random.Shared.NextDouble() * 5;
        var lat2 = lat1 + 0.001;
        var lon2 = lon1 + 0.001;
        var time1 = DateTimeOffset.UtcNow.AddMinutes(-10);
        var time2 = DateTimeOffset.UtcNow.AddMinutes(-5);

        await client.PostAsJsonAsync(
            $"/api/v1/vehicles/{vehicleId}/positions",
            new
            {
                source = "gps",
                points = new[]
                {
                    new { lat = lat1, lon = lon1, recordedAt = time1, accuracyM = 10.0 },
                    new { lat = lat2, lon = lon2, recordedAt = time2, accuracyM = 10.0 }
                }
            },
            Ct);

        var response = await client.GetAsync($"/api/v1/vehicles/{vehicleId}/route", Ct);

        var text = await response.Content.ReadAsStringAsync(Ct);
        Assert.True(response.StatusCode == HttpStatusCode.OK, text);
        var body = JsonDocument.Parse(text).RootElement;
        Assert.Equal("Feature", body.GetProperty("type").GetString());
        Assert.Equal("LineString", body.GetProperty("geometry").GetProperty("type").GetString());
        Assert.Equal(2, body.GetProperty("properties").GetProperty("pointCount").GetInt32());
    }

    [Fact]
    public async Task GetVehicleRoute_NoPositions_Returns404()
    {
        var login = Unique("driver.");
        var userId = await SeedUserAsync(login, "driver");
        var vehicleId = await SeedVehicleAsync(UniquePlate(), "car", userId);
        using var client = fixture.CreateClient("driver", userId);

        var response = await client.GetAsync($"/api/v1/vehicles/{vehicleId}/route", Ct);

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
    }

    [Fact]
    public async Task GetVehicleRoute_Anonymous_Returns401()
    {
        var login = Unique("driver.");
        var userId = await SeedUserAsync(login, "driver");
        var vehicleId = await SeedVehicleAsync(UniquePlate(), "car", userId);
        using var client = fixture.CreateClient();

        var response = await client.GetAsync($"/api/v1/vehicles/{vehicleId}/route", Ct);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }
}
