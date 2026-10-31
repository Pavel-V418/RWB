using App.Api.Security;
using App.Infrastructure.Security;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;
using Npgsql;
using Testcontainers.Kafka;
using Testcontainers.PostgreSql;

[assembly: AssemblyFixture(typeof(App.IntegrationTests.AppFixture))]

namespace App.IntegrationTests;

/// <summary>
/// Один набор контейнеров (PostGIS + Kafka) на все интеграционные тесты сборки.
/// Схема БД = db/*.sql каркаса + доменный DDL, если задана переменная APP_DOMAIN_SCHEMA.
/// </summary>
public sealed class AppFixture : IAsyncLifetime
{
    public const string PostgisImage = "imresamu/postgis:17-3.5";
    public const string KafkaImage = "apache/kafka:4.1.0";
    public const string JwtKey = "integration-test-signing-key-0123456789";

    private readonly PostgreSqlContainer _postgres = new PostgreSqlBuilder(PostgisImage)
        .WithDatabase("app")
        .WithUsername("app")
        .WithPassword("app")
        .Build();

    private readonly KafkaContainer _kafka = new KafkaBuilder(KafkaImage)
        .Build();

    public WebApplicationFactory<Program> Factory { get; private set; } = null!;

    public string KafkaBootstrap => _kafka.GetBootstrapAddress();

    public string ConnectionString => _postgres.GetConnectionString();

    public async ValueTask InitializeAsync()
    {
        await Task.WhenAll(_postgres.StartAsync(), _kafka.StartAsync());
        await ApplySchemaAsync();

        Factory = new WebApplicationFactory<Program>().WithWebHostBuilder(b =>
        {
            b.UseEnvironment("Testing");
            b.UseSetting("ConnectionStrings:Database", ConnectionString);
            b.UseSetting("Kafka:Enabled", "true");
            b.UseSetting("Kafka:BootstrapServers", KafkaBootstrap);
            b.UseSetting("Kafka:PollIntervalMs", "200");
            b.UseSetting("Jwt:SigningKey", JwtKey);
            b.UseSetting("RateLimiting:PermitPerMinute", "100000");
            b.UseSetting("OpenApi:Enabled", "true");
            b.ConfigureServices(s => s.AddControllers().AddApplicationPart(typeof(AppFixture).Assembly));
        });
    }

    public HttpClient CreateClient(string? role = null) =>
        role is null ? Factory.CreateClient() : CreateClient(role, Guid.NewGuid());

    /// <summary>Клиент с токеном пользователя userId (строку users с этим Id тест создаёт сам, если её требуют внешние ключи).</summary>
    public HttpClient CreateClient(string role, Guid userId) => Authorize(Factory.CreateClient(), role, userId);

    /// <summary>
    /// Клиент поверх хоста с заменёнными сервисами: внешние провайдеры (интерфейсы App.Application.Integrations) в тестах
    /// подменяются фейком, настоящих адресов у тестового хоста нет. БД и Kafka те же, что у <see cref="Factory"/>.
    /// </summary>
    public HttpClient CreateClient(string role, Guid userId, Action<IServiceCollection> configureServices) =>
        Authorize(Factory.WithWebHostBuilder(b => b.ConfigureTestServices(configureServices)).CreateClient(), role, userId);

    private static HttpClient Authorize(HttpClient client, string role, Guid userId)
    {
        var tokens = new JwtTokenService(Options.Create(new JwtOptions
        {
            Issuer = "app",
            Audience = "app",
            SigningKey = JwtKey,
            LifetimeMinutes = 30,
        }), TimeProvider.System);
        client.DefaultRequestHeaders.Authorization = new("Bearer", tokens.Issue(userId, "test-" + role, role).Token);
        return client;
    }

    public async ValueTask DisposeAsync()
    {
        if (Factory is not null)
        {
            await Factory.DisposeAsync();
        }

        await _kafka.DisposeAsync();
        await _postgres.DisposeAsync();
    }

    private async Task ApplySchemaAsync()
    {
        var files = Directory.GetFiles(Path.Combine(FindTemplateRoot(), "db"), "*.sql").Order().ToList();
        var domainSchema = Environment.GetEnvironmentVariable("APP_DOMAIN_SCHEMA");
        if (!string.IsNullOrEmpty(domainSchema))
        {
            files.Add(domainSchema);
        }

        await using var connection = new NpgsqlConnection(ConnectionString);
        await connection.OpenAsync();
        foreach (var file in files)
        {
            await using var command = new NpgsqlCommand(await File.ReadAllTextAsync(file), connection);
            await command.ExecuteNonQueryAsync();
        }
    }

    private static string FindTemplateRoot()
    {
        for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir is not null; dir = dir.Parent)
        {
            if (File.Exists(Path.Combine(dir.FullName, "db", "000_platform.sql")))
            {
                return dir.FullName;
            }
        }

        throw new DirectoryNotFoundException("Не найден каталог с db/000_platform.sql.");
    }
}
