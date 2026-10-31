using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using App.Application.Abstractions;
using App.Domain.Entities;
using App.Infrastructure.Persistence;
using Microsoft.Extensions.DependencyInjection;

namespace App.IntegrationTests;

public sealed class AuthApiTests(AppFixture fixture)
{
    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    [Fact]
    public async Task Login_IssuesToken_ThatWorksForMe_AndRejectsWrongPassword()
    {
        var login = "it." + Guid.NewGuid().ToString("N")[..8];
        await using (var scope = fixture.Factory.Services.CreateAsyncScope())
        {
            var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
            var hasher = scope.ServiceProvider.GetRequiredService<IPasswordHasher>();
            db.Users.Add(new User { Id = Guid.NewGuid(), Login = login, PasswordHash = hasher.Hash("Secret-123!"), Role = "admin", FullName = "IT" });
            await db.SaveChangesAsync(Ct);
        }

        using var client = fixture.CreateClient();
        var ok = await client.PostAsJsonAsync("/api/v1/auth/tokens", new { login, password = "Secret-123!" }, Ct);
        var wrong = await client.PostAsJsonAsync("/api/v1/auth/tokens", new { login, password = "Wrong-123!" }, Ct);
        var body = await ok.Content.ReadFromJsonAsync<JsonElement>(Ct);

        Assert.True(ok.StatusCode == HttpStatusCode.OK, await ok.Content.ReadAsStringAsync(Ct));
        Assert.Equal("Bearer", body.GetProperty("tokenType").GetString());
        Assert.Equal("admin", body.GetProperty("user").GetProperty("role").GetString());
        Assert.Equal(HttpStatusCode.Unauthorized, wrong.StatusCode);
        Assert.Equal("invalid_credentials", (await wrong.Content.ReadFromJsonAsync<JsonElement>(Ct)).GetProperty("code").GetString());

        client.DefaultRequestHeaders.Authorization = new("Bearer", body.GetProperty("accessToken").GetString());
        var me = await client.GetFromJsonAsync<JsonElement>("/api/v1/auth/me", Ct);
        Assert.Equal(login, me.GetProperty("login").GetString());
    }
}
