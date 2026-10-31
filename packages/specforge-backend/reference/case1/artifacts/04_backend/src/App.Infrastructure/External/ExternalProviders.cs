using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using App.Application.Common;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Http.Resilience;
using Microsoft.Extensions.Options;

namespace App.Infrastructure.External;

/// <summary>Настройки внешнего провайдера: секция <c>Providers:&lt;Name&gt;</c> (env <c>Providers__Name__BaseUrl</c>).</summary>
public sealed class ExternalProviderOptions
{
    public string BaseUrl { get; set; } = string.Empty;

    /// <summary>Ключ API провайдера, уходит заголовком <c>X-Api-Key</c>; хранить только в переменных окружения.</summary>
    public string? ApiKey { get; set; }

    /// <summary>Таймаут одной попытки.</summary>
    public int AttemptTimeoutSeconds { get; set; } = 5;

    public int Retries { get; set; } = 2;

    public int RetryDelayMs { get; set; } = 300;
}

/// <summary>
/// Шаблон адаптера внешнего провайдера: типизированный HttpClient + стандартная устойчивость
/// (повторы с backoff на 5xx/408/429, таймаут попытки, общий таймаут, circuit breaker).
/// Доменный адаптер — наследник <see cref="ExternalProviderClient"/>, интерфейс — в App.Application/Integrations.
/// </summary>
public static class ExternalProviderRegistration
{
    public static IHttpClientBuilder AddExternalProvider<TClient, TImplementation>(
        this IServiceCollection services, IConfiguration configuration, string name)
        where TClient : class
        where TImplementation : class, TClient
    {
        var section = configuration.GetSection($"Providers:{name}");
        services.Configure<ExternalProviderOptions>(name, section);
        var options = section.Get<ExternalProviderOptions>() ?? new ExternalProviderOptions();

        var builder = services.AddHttpClient<TClient, TImplementation>((sp, http) =>
        {
            var o = sp.GetRequiredService<IOptionsMonitor<ExternalProviderOptions>>().Get(name);
            if (Uri.TryCreate(o.BaseUrl, UriKind.Absolute, out var baseUri))
            {
                http.BaseAddress = baseUri;
            }

            if (!string.IsNullOrEmpty(o.ApiKey))
            {
                http.DefaultRequestHeaders.Add("X-Api-Key", o.ApiKey);
            }
        });

        builder.AddStandardResilienceHandler(r =>
        {
            var attempt = TimeSpan.FromSeconds(Math.Max(1, options.AttemptTimeoutSeconds));
            r.AttemptTimeout.Timeout = attempt;
            r.Retry.MaxRetryAttempts = Math.Max(0, options.Retries);
            r.Retry.Delay = TimeSpan.FromMilliseconds(Math.Max(1, options.RetryDelayMs));
            r.TotalRequestTimeout.Timeout = (attempt * (options.Retries + 1)) + TimeSpan.FromSeconds(5);
            r.CircuitBreaker.SamplingDuration = attempt * 2 > TimeSpan.FromSeconds(30) ? attempt * 2 : TimeSpan.FromSeconds(30);
        });
        return builder;
    }
}

/// <summary>
/// База доменного адаптера: JSON-запросы и перевод любых сбоев провайдера (сеть, таймаут, открытый
/// circuit breaker, ответ не 2xx) в <see cref="ExternalProviderException"/> → HTTP 502 нашего API.
/// </summary>
public abstract class ExternalProviderClient(HttpClient http, string providerName)
{
    protected static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);

    protected string ProviderName { get; } = providerName;

    protected async Task<T> SendAsync<T>(HttpMethod method, string path, object? body, string? idempotencyKey, CancellationToken cancellationToken)
    {
        using var request = new HttpRequestMessage(method, new Uri(path, UriKind.Relative));
        if (body is not null)
        {
            // Строка с Content-Length, а не поток (JsonContent идёт chunked): многие провайдеры chunked не принимают
            request.Content = new StringContent(JsonSerializer.Serialize(body, Json), System.Text.Encoding.UTF8, "application/json");
        }

        if (idempotencyKey is not null)
        {
            // Повтор после таймаута не должен создать вторую оплату у провайдера
            request.Headers.Add("Idempotency-Key", idempotencyKey);
        }

        try
        {
            using var response = await http.SendAsync(request, cancellationToken);
            if (!response.IsSuccessStatusCode)
            {
                var text = await response.Content.ReadAsStringAsync(cancellationToken);
                throw MapError(response.StatusCode, text);
            }

            return await response.Content.ReadFromJsonAsync<T>(Json, cancellationToken)
                ?? throw new ExternalProviderException(ProviderName, "пустой ответ");
        }
        catch (HttpRequestException e)
        {
            throw new ExternalProviderException(ProviderName, $"недоступен: {e.Message}");
        }
        catch (Polly.ExecutionRejectedException e)
        {
            throw new ExternalProviderException(ProviderName, $"не ответил вовремя или временно отключён ({e.GetType().Name})");
        }
        catch (TaskCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            throw new ExternalProviderException(ProviderName, "превышено время ожидания");
        }
        catch (JsonException e)
        {
            throw new ExternalProviderException(ProviderName, $"неожиданный формат ответа: {e.Message}");
        }
    }

    /// <summary>Перевод ответа не 2xx. Наследник может различать 404/409 провайдера и бросать доменные исключения.</summary>
    protected virtual AppException MapError(HttpStatusCode status, string body) =>
        new ExternalProviderException(ProviderName, $"HTTP {(int)status}: {(body.Length > 300 ? body[..300] : body)}");
}
