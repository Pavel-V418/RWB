using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using App.Api.Idempotency;
using App.Application.Abstractions;
using App.Domain.Common;
using App.Infrastructure.Messaging;
using App.Infrastructure.Persistence;
using Confluent.Kafka;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;

namespace App.IntegrationTests;

public sealed class PlatformIntegrationTests(AppFixture fixture)
{
    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    [Fact]
    public async Task HealthReady_IsHealthy_WhenPostgresAndKafkaAreUp()
    {
        using var client = fixture.CreateClient();

        var response = await client.GetAsync(new Uri("/health/ready", UriKind.Relative), Ct);
        var body = await response.Content.ReadFromJsonAsync<JsonElement>(Ct);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal("Healthy", body.GetProperty("status").GetString());
        var checks = body.GetProperty("checks").EnumerateArray().Select(c => c.GetProperty("name").GetString()).ToList();
        Assert.Contains("postgres", checks);
        Assert.Contains("kafka", checks);
    }

    [Fact]
    public async Task Health_ReportsAllChecks_IncludingBusinessOutbox()
    {
        using var client = fixture.CreateClient();

        var response = await client.GetAsync(new Uri("/health", UriKind.Relative), Ct);
        var body = await response.Content.ReadFromJsonAsync<JsonElement>(Ct);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Contains(body.GetProperty("status").GetString(), new[] { "Healthy", "Degraded" });
        var checks = body.GetProperty("checks").EnumerateArray().Select(c => c.GetProperty("name").GetString()).ToList();
        Assert.Contains("outbox", checks);
    }

    [Fact]
    public async Task Metrics_ExposesPrometheusFormat()
    {
        using var client = fixture.CreateClient();
        await client.GetAsync(new Uri("/health", UriKind.Relative), Ct);

        var text = await client.GetStringAsync(new Uri("/metrics", UriKind.Relative), Ct);

        Assert.Contains("http_requests_received_total", text, StringComparison.Ordinal);
        Assert.Contains("aspnetcore_healthcheck_status", text, StringComparison.Ordinal);
    }

    [Fact]
    public async Task Restart_IsAdminOnly()
    {
        using var anonymous = fixture.CreateClient();
        using var driver = fixture.CreateClient("driver");

        var anonymousResponse = await anonymous.PostAsync(new Uri("/api/v1/system/restarts", UriKind.Relative), null, Ct);
        var driverResponse = await driver.PostAsync(new Uri("/api/v1/system/restarts", UriKind.Relative), null, Ct);

        Assert.Equal(HttpStatusCode.Unauthorized, anonymousResponse.StatusCode);
        Assert.Equal(HttpStatusCode.Forbidden, driverResponse.StatusCode);
    }

    [Fact]
    public async Task Swagger_IsServedAtContractDiffPath()
    {
        using var client = fixture.CreateClient();

        var doc = await client.GetFromJsonAsync<JsonElement>(new Uri("/swagger/v1/swagger.json", UriKind.Relative), Ct);
        var ui = await client.GetAsync(new Uri("/swagger/index.html", UriKind.Relative), Ct);

        Assert.StartsWith("3.", doc.GetProperty("openapi").GetString(), StringComparison.Ordinal);
        Assert.Equal(HttpStatusCode.OK, ui.StatusCode);
    }

    [Fact]
    public async Task RequestId_FromProxy_IsEchoed()
    {
        using var client = fixture.CreateClient();
        using var request = new HttpRequestMessage(HttpMethod.Get, "/api/v1/system/info");
        request.Headers.Add("X-Request-Id", "req-123");
        request.Headers.Add("X-Forwarded-For", "203.0.113.7");

        var response = await client.SendAsync(request, Ct);

        Assert.Equal("req-123", response.Headers.GetValues("X-Request-Id").Single());
    }

    [Fact]
    public async Task CorrelationId_IsEchoed()
    {
        using var client = fixture.CreateClient();
        using var request = new HttpRequestMessage(HttpMethod.Get, "/api/v1/system/info");
        request.Headers.Add("X-Correlation-Id", "corr-42");

        var response = await client.SendAsync(request, Ct);

        Assert.Equal("corr-42", response.Headers.GetValues("X-Correlation-Id").Single());
    }

    [Fact]
    public async Task ExpiredToken_Returns401ProblemJson()
    {
        var past = new FixedTimeProvider(DateTimeOffset.UtcNow.AddDays(-2));
        var tokens = new App.Infrastructure.Security.JwtTokenService(Microsoft.Extensions.Options.Options.Create(new App.Infrastructure.Security.JwtOptions
        {
            Issuer = "app",
            Audience = "app",
            SigningKey = AppFixture.JwtKey,
            LifetimeMinutes = 30,
        }), past);
        using var client = fixture.CreateClient();
        client.DefaultRequestHeaders.Authorization = new("Bearer", tokens.Issue(Guid.NewGuid(), "old", "driver").Token);

        var response = await client.PostAsync(new Uri("/hubs/events/negotiate?negotiateVersion=1", UriKind.Relative), null, Ct);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
        Assert.Equal("application/problem+json", response.Content.Headers.ContentType?.MediaType);
    }

    private sealed class FixedTimeProvider(DateTimeOffset now) : TimeProvider
    {
        public override DateTimeOffset GetUtcNow() => now;
    }

    [Fact]
    public async Task CreateClient_WithServiceOverride_UsesReplacedService()
    {
        var fixedNow = new DateTimeOffset(2030, 1, 2, 3, 4, 5, TimeSpan.Zero);
        using var client = fixture.CreateClient("driver", Guid.NewGuid(), s => s.AddSingleton<TimeProvider>(new FixedTimeProvider(fixedNow)));

        var info = await client.GetFromJsonAsync<JsonElement>(new Uri("/api/v1/system/info", UriKind.Relative), Ct);

        Assert.StartsWith("2030-01-02T03:04:05", info.GetProperty("serverTime").GetString(), StringComparison.Ordinal);
    }

    [Fact]
    public async Task RequestValidationException_Returns400WithFieldErrors()
    {
        using var client = fixture.CreateClient("driver");

        var response = await client.GetAsync(new Uri("/api/v1/test/invalid", UriKind.Relative), Ct);

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.Equal("application/problem+json", response.Content.Headers.ContentType?.MediaType);
        var problem = await response.Content.ReadFromJsonAsync<JsonElement>(Ct);
        Assert.Equal("validation_failed", problem.GetProperty("code").GetString());
        Assert.Equal("из будущего", problem.GetProperty("errors").GetProperty("Points[0].RecordedAt")[0].GetString());
    }

    [Fact]
    public async Task ProtectedEndpoint_WithoutToken_Returns401ProblemJson()
    {
        using var client = fixture.CreateClient();

        var response = await client.PostAsync(new Uri("/hubs/events/negotiate?negotiateVersion=1", UriKind.Relative), null, Ct);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
        Assert.Equal("application/problem+json", response.Content.Headers.ContentType?.MediaType);
        var problem = await response.Content.ReadFromJsonAsync<JsonElement>(Ct);
        Assert.Equal("unauthorized", problem.GetProperty("code").GetString());
    }

    [Fact]
    public async Task SystemInfo_IsAnonymous()
    {
        using var client = fixture.CreateClient();

        var response = await client.GetAsync(new Uri("/api/v1/system/info", UriKind.Relative), Ct);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }

    [Fact]
    public async Task Outbox_PublishesEventToKafka_AndMarksRowPublished()
    {
        var itemId = Guid.NewGuid();
        var topic = "it-" + Guid.NewGuid().ToString("N");
        using (var admin = new AdminClientBuilder(new AdminClientConfig { BootstrapServers = fixture.KafkaBootstrap }).Build())
        {
            await admin.CreateTopicsAsync([new Confluent.Kafka.Admin.TopicSpecification { Name = topic, NumPartitions = 1, ReplicationFactor = 1 }]);
        }

        using var consumer = new ConsumerBuilder<string, string>(new ConsumerConfig
        {
            BootstrapServers = fixture.KafkaBootstrap,
            GroupId = "it-" + Guid.NewGuid().ToString("N"),
            AutoOffsetReset = AutoOffsetReset.Earliest,
            AllowAutoCreateTopics = true,
        }).Build();
        consumer.Subscribe(topic);

        await using (var scope = fixture.Factory.Services.CreateAsyncScope())
        {
            var outbox = scope.ServiceProvider.GetRequiredService<IOutbox>();
            var db = scope.ServiceProvider.GetRequiredService<IAppDbContext>();
            outbox.Enqueue(new ItemShipped(itemId, topic, DateTimeOffset.UtcNow));
            await db.SaveChangesAsync(Ct);
        }

        ConsumeResult<string, string>? message = null;
        var deadline = DateTime.UtcNow.AddSeconds(30);
        while (message is null && DateTime.UtcNow < deadline)
        {
            message = consumer.Consume(TimeSpan.FromSeconds(1));
        }

        Assert.NotNull(message);
        Assert.Equal(itemId.ToString(), message.Message.Key);
        var envelope = EventEnvelope.FromJson(message.Message.Value)!;
        Assert.Equal("ItemShipped", envelope.Type);
        Assert.Equal(itemId.ToString(), (string)envelope.Payload["itemId"]!);

        await using var verify = fixture.Factory.Services.CreateAsyncScope();
        var row = await verify.ServiceProvider.GetRequiredService<AppDbContext>()
            .OutboxMessages.AsNoTracking().SingleAsync(m => m.Id == envelope.EventId, Ct);
        Assert.NotNull(row.PublishedAt);
    }

    [Fact]
    public async Task Idempotency_ReplaysSameResponse_AndRejectsKeyReuseWithOtherBody()
    {
        using var client = fixture.CreateClient("driver");
        var key = "key-" + Guid.NewGuid().ToString("N");

        var first = await PostEchoAsync(client, key, "a");
        var second = await PostEchoAsync(client, key, "a");
        var reused = await PostEchoAsync(client, key, "b");
        var missing = await PostEchoAsync(client, null, "a");

        Assert.Equal(HttpStatusCode.Created, first.StatusCode);
        Assert.Equal(HttpStatusCode.Created, second.StatusCode);
        Assert.Equal(await first.Content.ReadAsStringAsync(Ct), await second.Content.ReadAsStringAsync(Ct));
        Assert.Equal(HttpStatusCode.UnprocessableEntity, reused.StatusCode);
        Assert.Equal(HttpStatusCode.BadRequest, missing.StatusCode);
    }

    private static async Task<HttpResponseMessage> PostEchoAsync(HttpClient client, string? key, string text)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, "/api/v1/test/echo")
        {
            Content = JsonContent.Create(new { text }),
        };
        if (key is not null)
        {
            request.Headers.Add(IdempotencyFilter.HeaderName, key);
        }

        return await client.SendAsync(request, Ct);
    }

    private sealed record ItemShipped(Guid ItemId, string TopicName, DateTimeOffset OccurredAt) : IIntegrationEvent
    {
        public string EventType => "ItemShipped";

        public int Version => 1;

        public string Topic => TopicName;

        public string PartitionKey => ItemId.ToString();
    }
}

/// <summary>Тестовый контроллер для проверки фильтра идемпотентности.</summary>
[ApiController]
[Route("api/v1/test")]
[Authorize]
public sealed class TestEchoController : ControllerBase
#pragma warning disable CA1822 // экшены MVC — методы экземпляра
{
    [HttpPost("echo")]
    [Idempotent]
    public IActionResult Echo([FromBody] EchoRequest request) =>
        new ObjectResult(new { id = Guid.NewGuid(), request.Text }) { StatusCode = StatusCodes.Status201Created };

    [HttpGet("invalid")]
    public IActionResult Invalid() =>
        throw new App.Application.Common.RequestValidationException("Points[0].RecordedAt", "из будущего");
#pragma warning restore CA1822
}

public sealed record EchoRequest(string Text);
