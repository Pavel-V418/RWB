using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using App.Application.Abstractions;
using App.Domain.Common;
using App.Domain.Entities;
using App.Infrastructure.Persistence;
using Microsoft.Extensions.DependencyInjection;
using NetTopologySuite.Geometries;

namespace App.IntegrationTests.Features;

/// <summary>Интеграционные тесты фичи Vehicles: список, создание, детальный статус.</summary>
public sealed class VehiclesApiTests(AppFixture fixture)
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

    [Fact]
    public async Task ListVehicles_ReturnsPagedVehicles_ForAdmin()
    {
        var login = Unique("admin.");
        var adminId = await SeedUserAsync(login, "admin");
        var plate = UniquePlate();
        var vehicleId = await SeedVehicleAsync(plate, "car", null, "Test Car");

        using var client = fixture.CreateClient("admin", adminId);
        // БД общая для всех тестов: своя запись может оказаться не на первой странице — листаем до неё
        JsonElement? found = null;
        for (var page = 1; found is null; page++)
        {
            var response = await client.GetAsync($"/api/v1/vehicles?page={page}&pageSize=100", Ct);
            var text = await response.Content.ReadAsStringAsync(Ct);
            Assert.True(response.StatusCode == HttpStatusCode.OK, text);
            var body = JsonDocument.Parse(text).RootElement;
            var meta = body.GetProperty("meta");
            Assert.Equal(page, meta.GetProperty("page").GetInt32());
            Assert.Equal(100, meta.GetProperty("pageSize").GetInt32());
            var items = body.GetProperty("items");
            foreach (var item in items.EnumerateArray())
            {
                if (item.GetProperty("id").GetGuid() == vehicleId)
                {
                    found = item.Clone();
                }
            }
            Assert.True(found is not null || items.GetArrayLength() == 100, "Seeded vehicle not found in admin list");
        }

        Assert.Equal(plate, found.Value.GetProperty("plateNumber").GetString());
        Assert.Equal("car", found.Value.GetProperty("vehicleType").GetString());
        Assert.True(found.Value.GetProperty("isActive").GetBoolean());
    }

    [Fact]
    public async Task ListVehicles_ReturnsOnlyOwnVehicles_ForDriver()
    {
        var login = Unique("driver.");
        var driverId = await SeedUserAsync(login, "driver");
        var otherLogin = Unique("other.");
        var otherDriverId = await SeedUserAsync(otherLogin, "driver");

        var ownPlate = UniquePlate();
        var ownVehicleId = await SeedVehicleAsync(ownPlate, "car", driverId);
        var otherPlate = UniquePlate();
        var otherVehicleId = await SeedVehicleAsync(otherPlate, "car", otherDriverId);

        using var client = fixture.CreateClient("driver", driverId);
        var response = await client.GetAsync("/api/v1/vehicles?page=1&pageSize=100", Ct);

        var text = await response.Content.ReadAsStringAsync(Ct);
        Assert.True(response.StatusCode == HttpStatusCode.OK, text);
        var body = JsonDocument.Parse(text).RootElement;
        var items = body.GetProperty("items");
        var foundOwn = false;
        var foundOther = false;
        foreach (var item in items.EnumerateArray())
        {
            var id = item.GetProperty("id").GetGuid();
            if (id == ownVehicleId) foundOwn = true;
            if (id == otherVehicleId) foundOther = true;
        }
        Assert.True(foundOwn, "Driver's own vehicle should be in list");
        Assert.False(foundOther, "Other driver's vehicle should NOT be in list");
    }

    [Fact]
    public async Task CreateVehicle_CreatesVehicle_ForAdmin()
    {
        var login = Unique("admin.");
        var adminId = await SeedUserAsync(login, "admin");
        var plate = UniquePlate();

        using var client = fixture.CreateClient("admin", adminId);
        var response = await client.PostAsJsonAsync("/api/v1/vehicles", new
        {
            plateNumber = plate,
            vehicleType = "truck",
            model = "Volvo FH",
            driverId = (Guid?)null
        }, Ct);

        var text = await response.Content.ReadAsStringAsync(Ct);
        Assert.True(response.StatusCode == HttpStatusCode.Created, text);
        var body = JsonDocument.Parse(text).RootElement;
        Assert.Equal(plate, body.GetProperty("plateNumber").GetString());
        Assert.Equal("truck", body.GetProperty("vehicleType").GetString());
        Assert.Equal("Volvo FH", body.GetProperty("model").GetString());
        Assert.True(body.GetProperty("isActive").GetBoolean());
        Assert.Equal(JsonValueKind.Null, body.GetProperty("driverId").ValueKind);
    }

    [Fact]
    public async Task CreateVehicle_Returns409_ForDuplicatePlate()
    {
        var login = Unique("admin.");
        var adminId = await SeedUserAsync(login, "admin");
        var plate = UniquePlate();
        await SeedVehicleAsync(plate, "car", null);

        using var client = fixture.CreateClient("admin", adminId);
        var response = await client.PostAsJsonAsync("/api/v1/vehicles", new
        {
            plateNumber = plate,
            vehicleType = "car",
            model = "Duplicate"
        }, Ct);

        Assert.Equal(HttpStatusCode.Conflict, response.StatusCode);
        var problem = await response.Content.ReadFromJsonAsync<JsonElement>(Ct);
        Assert.Equal("plate_exists", problem.GetProperty("code").GetString());
    }

    [Fact]
    public async Task CreateVehicle_Returns403_ForDriver()
    {
        var login = Unique("driver.");
        var driverId = await SeedUserAsync(login, "driver");
        var plate = UniquePlate();

        using var client = fixture.CreateClient("driver", driverId);
        var response = await client.PostAsJsonAsync("/api/v1/vehicles", new
        {
            plateNumber = plate,
            vehicleType = "car"
        }, Ct);

        Assert.Equal(HttpStatusCode.Forbidden, response.StatusCode);
    }

    [Fact]
    public async Task GetVehicle_ReturnsVehicleStatus_ForAdmin()
    {
        var login = Unique("admin.");
        var adminId = await SeedUserAsync(login, "admin");
        var plate = UniquePlate();
        var vehicleId = await SeedVehicleAsync(plate, "car", null, "Status Car");

        using var client = fixture.CreateClient("admin", adminId);
        var response = await client.GetAsync($"/api/v1/vehicles/{vehicleId}", Ct);

        var text = await response.Content.ReadAsStringAsync(Ct);
        Assert.True(response.StatusCode == HttpStatusCode.OK, text);
        var body = JsonDocument.Parse(text).RootElement;
        Assert.Equal(vehicleId, body.GetProperty("id").GetGuid());
        Assert.Equal(plate, body.GetProperty("plateNumber").GetString());
        Assert.Equal("car", body.GetProperty("vehicleType").GetString());
        Assert.Equal("Status Car", body.GetProperty("model").GetString());
        Assert.True(body.GetProperty("isActive").GetBoolean());
        Assert.Equal(JsonValueKind.Null, body.GetProperty("currentZone").ValueKind);
        Assert.Equal(JsonValueKind.Null, body.GetProperty("lastPosition").ValueKind);
        Assert.Equal(JsonValueKind.Null, body.GetProperty("activeSession").ValueKind);
    }

    [Fact]
    public async Task GetVehicle_Returns404_ForNonExistentVehicle()
    {
        var login = Unique("admin.");
        var adminId = await SeedUserAsync(login, "admin");
        var nonExistentId = Guid.NewGuid();

        using var client = fixture.CreateClient("admin", adminId);
        var response = await client.GetAsync($"/api/v1/vehicles/{nonExistentId}", Ct);

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
    }

    [Fact]
    public async Task GetVehicle_Returns403_ForDriverWithoutAccess()
    {
        var login = Unique("driver.");
        var driverId = await SeedUserAsync(login, "driver");
        var otherLogin = Unique("other.");
        var otherDriverId = await SeedUserAsync(otherLogin, "driver");
        var plate = UniquePlate();
        var vehicleId = await SeedVehicleAsync(plate, "car", otherDriverId);

        using var client = fixture.CreateClient("driver", driverId);
        var response = await client.GetAsync($"/api/v1/vehicles/{vehicleId}", Ct);

        Assert.Equal(HttpStatusCode.Forbidden, response.StatusCode);
    }
}
