using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using App.Application.Abstractions;
using App.Domain.Entities;
using App.Infrastructure.Persistence;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;

namespace App.IntegrationTests.Features;

/// <summary>Интеграционные тесты фичи Notifications: список уведомлений и смена статуса прочтения.</summary>
public sealed class NotificationsApiTests(AppFixture fixture)
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

    private async Task<Guid> SeedNotificationAsync(Guid vehicleId, Guid? userId, string kind, string severity, string title, string body, DateTimeOffset? readAt = null, Guid? eventId = null)
    {
        await using var scope = fixture.Factory.Services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        var notification = new Notification
        {
            Id = Guid.NewGuid(),
            VehicleId = vehicleId,
            UserId = userId,
            EventId = eventId,
            Kind = kind,
            Severity = severity,
            Title = title,
            Body = body,
            CreatedAt = DateTimeOffset.UtcNow,
            ReadAt = readAt
        };
        db.Notifications.Add(notification);
        await db.SaveChangesAsync(Ct);
        return notification.Id;
    }

    [Fact]
    public async Task ListNotifications_ReturnsItems_ForDriverVehicle()
    {
        var login = Unique("drv.");
        var userId = await SeedUserAsync(login, "driver");
        var vehicleId = await SeedVehicleAsync(UniquePlate(), "car", userId);
        var notifId = await SeedNotificationAsync(vehicleId, userId, "zone_entered", "info", "Test Title", "Test Body");

        using var client = fixture.CreateClient("driver", userId);
        var response = await client.GetAsync($"/api/v1/notifications?vehicleId={vehicleId}&unreadOnly=false&page=1&pageSize=10", Ct);

        var text = await response.Content.ReadAsStringAsync(Ct);
        Assert.True(response.StatusCode == HttpStatusCode.OK, text);
        var body = JsonDocument.Parse(text).RootElement;
        Assert.Equal(1, body.GetProperty("meta").GetProperty("total").GetInt32());
        Assert.Equal(notifId, body.GetProperty("items")[0].GetProperty("id").GetGuid());
        Assert.Equal("zone_entered", body.GetProperty("items")[0].GetProperty("kind").GetString());
    }

    [Fact]
    public async Task ListNotifications_FiltersUnread_WhenUnreadOnlyIsTrue()
    {
        var login = Unique("drv.");
        var userId = await SeedUserAsync(login, "driver");
        var vehicleId = await SeedVehicleAsync(UniquePlate(), "car", userId);
        await SeedNotificationAsync(vehicleId, userId, "zone_entered", "info", "Unread", "Body", readAt: null);
        await SeedNotificationAsync(vehicleId, userId, "session_paid", "info", "Read", "Body", readAt: DateTimeOffset.UtcNow);

        using var client = fixture.CreateClient("driver", userId);
        var response = await client.GetAsync($"/api/v1/notifications?vehicleId={vehicleId}&unreadOnly=true&page=1&pageSize=10", Ct);

        var text = await response.Content.ReadAsStringAsync(Ct);
        Assert.True(response.StatusCode == HttpStatusCode.OK, text);
        var body = JsonDocument.Parse(text).RootElement;
        Assert.Equal(1, body.GetProperty("meta").GetProperty("total").GetInt32());
        Assert.Equal("Unread", body.GetProperty("items")[0].GetProperty("title").GetString());
    }

    [Fact]
    public async Task ListNotifications_Returns404_ForNonExistentVehicle()
    {
        var login = Unique("drv.");
        var userId = await SeedUserAsync(login, "driver");
        var nonExistentVehicleId = Guid.NewGuid();

        using var client = fixture.CreateClient("driver", userId);
        var response = await client.GetAsync($"/api/v1/notifications?vehicleId={nonExistentVehicleId}&unreadOnly=false&page=1&pageSize=10", Ct);

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
    }

    [Fact]
    public async Task ListNotifications_Returns403_ForOtherDriverVehicle()
    {
        var login1 = Unique("drv1.");
        var userId1 = await SeedUserAsync(login1, "driver");
        var login2 = Unique("drv2.");
        var userId2 = await SeedUserAsync(login2, "driver");
        var vehicleId = await SeedVehicleAsync(UniquePlate(), "car", userId1);

        using var client = fixture.CreateClient("driver", userId2);
        var response = await client.GetAsync($"/api/v1/notifications?vehicleId={vehicleId}&unreadOnly=false&page=1&pageSize=10", Ct);

        Assert.Equal(HttpStatusCode.Forbidden, response.StatusCode);
    }

    [Fact]
    public async Task ListNotifications_Returns400_ForInvalidPage()
    {
        var login = Unique("drv.");
        var userId = await SeedUserAsync(login, "driver");
        var vehicleId = await SeedVehicleAsync(UniquePlate(), "car", userId);

        using var client = fixture.CreateClient("driver", userId);
        var response = await client.GetAsync($"/api/v1/notifications?vehicleId={vehicleId}&unreadOnly=false&page=0&pageSize=10", Ct);

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        var problem = await response.Content.ReadFromJsonAsync<JsonElement>(Ct);
        Assert.Equal("validation_failed", problem.GetProperty("code").GetString());
    }

    [Fact]
    public async Task MarkNotificationRead_SetsReadAt_WhenReadIsTrue()
    {
        var login = Unique("drv.");
        var userId = await SeedUserAsync(login, "driver");
        var vehicleId = await SeedVehicleAsync(UniquePlate(), "car", userId);
        var notifId = await SeedNotificationAsync(vehicleId, userId, "zone_entered", "info", "Title", "Body", readAt: null);

        using var client = fixture.CreateClient("driver", userId);
        var response = await client.PatchAsJsonAsync($"/api/v1/notifications/{notifId}", new { read = true }, Ct);

        Assert.Equal(HttpStatusCode.NoContent, response.StatusCode);

        await using var scope = fixture.Factory.Services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        var notification = await db.Notifications.AsNoTracking().FirstAsync(n => n.Id == notifId, Ct);
        Assert.NotNull(notification.ReadAt);
    }

    [Fact]
    public async Task MarkNotificationRead_ClearsReadAt_WhenReadIsFalse()
    {
        var login = Unique("drv.");
        var userId = await SeedUserAsync(login, "driver");
        var vehicleId = await SeedVehicleAsync(UniquePlate(), "car", userId);
        var notifId = await SeedNotificationAsync(vehicleId, userId, "zone_entered", "info", "Title", "Body", readAt: DateTimeOffset.UtcNow);

        using var client = fixture.CreateClient("driver", userId);
        var response = await client.PatchAsJsonAsync($"/api/v1/notifications/{notifId}", new { read = false }, Ct);

        Assert.Equal(HttpStatusCode.NoContent, response.StatusCode);

        await using var scope = fixture.Factory.Services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        var notification = await db.Notifications.AsNoTracking().FirstAsync(n => n.Id == notifId, Ct);
        Assert.Null(notification.ReadAt);
    }

    [Fact]
    public async Task MarkNotificationRead_Returns403_ForOtherUserNotification()
    {
        var login1 = Unique("drv1.");
        var userId1 = await SeedUserAsync(login1, "driver");
        var login2 = Unique("drv2.");
        var userId2 = await SeedUserAsync(login2, "driver");
        var vehicleId = await SeedVehicleAsync(UniquePlate(), "car", userId1);
        var notifId = await SeedNotificationAsync(vehicleId, userId1, "zone_entered", "info", "Title", "Body");

        using var client = fixture.CreateClient("driver", userId2);
        var response = await client.PatchAsJsonAsync($"/api/v1/notifications/{notifId}", new { read = true }, Ct);

        Assert.Equal(HttpStatusCode.Forbidden, response.StatusCode);
    }
}
