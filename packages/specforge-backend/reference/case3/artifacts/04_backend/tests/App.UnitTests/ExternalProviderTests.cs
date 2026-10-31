using System.Net;
using App.Application.Common;
using App.Infrastructure.External;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;

namespace App.UnitTests;

public sealed class ExternalProviderTests
{
    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    [Fact]
    public async Task TransientFailures_AreRetried_ThenSucceed()
    {
        var handler = new ScriptedHandler(HttpStatusCode.ServiceUnavailable, HttpStatusCode.ServiceUnavailable, HttpStatusCode.OK);
        var client = Build(handler);

        var result = await client.PingAsync(Ct);

        Assert.Equal("pong", result.Value);
        Assert.Equal(3, handler.Calls);
        Assert.Equal("key-1", handler.LastIdempotencyKey);
    }

    [Fact]
    public async Task PersistentFailure_BecomesExternalProviderException()
    {
        var handler = new ScriptedHandler(HttpStatusCode.ServiceUnavailable, HttpStatusCode.ServiceUnavailable, HttpStatusCode.ServiceUnavailable);
        var client = Build(handler);

        var error = await Assert.ThrowsAsync<ExternalProviderException>(() => client.PingAsync(Ct));

        Assert.Equal(502, error.StatusCode);
        Assert.Contains("Demo", error.Message, StringComparison.Ordinal);
    }

    private static ITestProvider Build(ScriptedHandler handler)
    {
        var config = new ConfigurationBuilder().AddInMemoryCollection(new Dictionary<string, string?>
        {
            ["Providers:Demo:BaseUrl"] = "http://provider.test/",
            ["Providers:Demo:Retries"] = "2",
            ["Providers:Demo:RetryDelayMs"] = "1",
        }).Build();
        var services = new ServiceCollection();
        services.AddExternalProvider<ITestProvider, TestProvider>(config, "Demo").ConfigurePrimaryHttpMessageHandler(() => handler);
        return services.BuildServiceProvider().GetRequiredService<ITestProvider>();
    }

    public interface ITestProvider
    {
        Task<Pong> PingAsync(CancellationToken cancellationToken);
    }

    public sealed record Pong(string Value);

    private sealed class TestProvider(HttpClient http) : ExternalProviderClient(http, "Demo"), ITestProvider
    {
        public Task<Pong> PingAsync(CancellationToken cancellationToken) =>
            SendAsync<Pong>(HttpMethod.Post, "ping", new { }, "key-1", cancellationToken);
    }

    private sealed class ScriptedHandler(params HttpStatusCode[] script) : HttpMessageHandler
    {
        public int Calls { get; private set; }

        public string? LastIdempotencyKey { get; private set; }

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            var status = script[Math.Min(Calls, script.Length - 1)];
            Calls++;
            LastIdempotencyKey = request.Headers.TryGetValues("Idempotency-Key", out var v) ? v.Single() : null;
            return Task.FromResult(new HttpResponseMessage(status)
            {
                Content = new StringContent("""{"value":"pong"}""", System.Text.Encoding.UTF8, "application/json"),
            });
        }
    }
}
