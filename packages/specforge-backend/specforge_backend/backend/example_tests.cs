using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using App.Application.Abstractions;
using App.Domain.Entities;
using App.Infrastructure.Persistence;
using Microsoft.Extensions.DependencyInjection;

namespace App.IntegrationTests.Features;

/// <summary>Эталон тестов фичи: HTTP через WebApplicationFactory, данные — в общей БД PostGIS, у каждого теста свои.</summary>
public sealed class AuthApiTests(AppFixture fixture)
{
    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    // Тесты классов идут параллельно в одной БД: уникальные значения и никаких расчётов на пустые таблицы
    private static string Unique(string prefix) => prefix + Guid.NewGuid().ToString("N")[..8];

    private async Task<Guid> SeedUserAsync(string login, string role, string password = "Secret-123!")
    {
        await using var scope = fixture.Factory.Services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        var hasher = scope.ServiceProvider.GetRequiredService<IPasswordHasher>();
        var user = new User { Id = Guid.NewGuid(), Login = login, PasswordHash = hasher.Hash(password), Role = role, FullName = "IT" };
        db.Users.Add(user);
        await db.SaveChangesAsync(Ct);
        return user.Id;
    }

    [Fact]
    public async Task Login_IssuesToken_ForValidPassword()
    {
        var login = Unique("it.");
        await SeedUserAsync(login, "admin");
        using var client = fixture.CreateClient();

        var response = await client.PostAsJsonAsync("/api/v1/auth/tokens", new { login, password = "Secret-123!" }, Ct);

        var text = await response.Content.ReadAsStringAsync(Ct);
        Assert.True(response.StatusCode == HttpStatusCode.OK, text);
        var body = JsonDocument.Parse(text).RootElement;
        Assert.Equal("admin", body.GetProperty("user").GetProperty("role").GetString());
    }

    [Fact]
    public async Task Login_WrongPassword_Returns401WithCode()
    {
        var login = Unique("it.");
        await SeedUserAsync(login, "driver");
        using var client = fixture.CreateClient();

        var response = await client.PostAsJsonAsync("/api/v1/auth/tokens", new { login, password = "Wrong-123!" }, Ct);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
        var problem = await response.Content.ReadFromJsonAsync<JsonElement>(Ct);
        Assert.Equal("invalid_credentials", problem.GetProperty("code").GetString());
    }

    [Fact]
    public async Task Me_ReturnsCurrentUser_ForTokenOfSeededUser()
    {
        var login = Unique("it.");
        var userId = await SeedUserAsync(login, "driver");
        // Токен с Id существующего пользователя — когда внешние ключи ссылаются на users
        using var client = fixture.CreateClient("driver", userId);

        var me = await client.GetFromJsonAsync<JsonElement>("/api/v1/auth/me", Ct);

        Assert.Equal(login, me.GetProperty("login").GetString());
    }
}
