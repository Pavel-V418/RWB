using System.Diagnostics;
using System.Net.Mime;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading.RateLimiting;
using App.Api.Errors;
using App.Api.Realtime;
using App.Api.Security;
using App.Application.Abstractions;
using App.Application.Common;
using App.Infrastructure;
using App.Infrastructure.Messaging;
using App.Infrastructure.Security;
using Confluent.Kafka;
using Microsoft.AspNetCore.Authentication.JwtBearer;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Diagnostics.HealthChecks;
using Microsoft.AspNetCore.HttpOverrides;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Extensions.Diagnostics.HealthChecks;
using NetTopologySuite.IO.Converters;
using Prometheus;

namespace App.Api;

/// <summary>Сквозная настройка API. Доменный код сюда не добавляется.</summary>
internal static class ApiSetup
{
    public const string CorsPolicy = "default";

    public static void ConfigureJson(JsonSerializerOptions options)
    {
        options.PropertyNamingPolicy = JsonNamingPolicy.CamelCase;
        options.DefaultIgnoreCondition = JsonIgnoreCondition.Never;
        options.Converters.Add(new JsonStringEnumConverter());
        options.Converters.Add(new GeoJsonConverterFactory());
    }

    public static IServiceCollection AddApi(this IServiceCollection services, IConfiguration configuration)
    {
        services.AddControllers().AddJsonOptions(o => ConfigureJson(o.JsonSerializerOptions));
        services.AddSignalR().AddJsonProtocol(o => ConfigureJson(o.PayloadSerializerOptions));
        services.AddOpenApi();
        // За nginx: реальный IP клиента (rate limit, логи) и схема из X-Forwarded-*.
        // Доверяем любому прокси: API доступен только из внутренней сети compose/стенда.
        services.Configure<ForwardedHeadersOptions>(o =>
        {
            o.ForwardedHeaders = ForwardedHeaders.XForwardedFor | ForwardedHeaders.XForwardedProto | ForwardedHeaders.XForwardedHost;
            o.KnownIPNetworks.Clear();
            o.KnownProxies.Clear();
        });
        services.AddHttpContextAccessor();

        services.AddProblemDetails(o => o.CustomizeProblemDetails = ctx =>
        {
            ctx.ProblemDetails.Extensions["traceId"] = Activity.Current?.Id ?? ctx.HttpContext.TraceIdentifier;
            if (!ctx.ProblemDetails.Extensions.ContainsKey("code"))
            {
                ctx.ProblemDetails.Extensions["code"] = ctx.ProblemDetails.Status switch
                {
                    400 => "validation_failed",
                    401 => "unauthorized",
                    403 => "forbidden",
                    404 => "not_found",
                    405 => "method_not_allowed",
                    _ => "error",
                };
            }
        });
        services.AddExceptionHandler<AppExceptionHandler>();

        services.AddScoped<ICurrentUser, CurrentUser>();
        services.AddScoped<IRequestContext, RequestContext>();
        services.AddScoped<ISubscriptionGuard, AdminOnlySubscriptionGuard>();
        services.AddSingleton<IRealtimeNotifier, SignalRRealtimeNotifier>();

        AddAuth(services, configuration);
        AddRateLimiting(services, configuration);
        AddHealth(services, configuration);

        var origins = configuration.GetSection("Cors:AllowedOrigins").Get<string[]>() ?? [];
        services.AddCors(o => o.AddPolicy(CorsPolicy, p => p
            .WithOrigins(origins)
            .AllowAnyHeader()
            .AllowAnyMethod()
            .AllowCredentials()));

        return services;
    }

    private static void AddAuth(IServiceCollection services, IConfiguration configuration)
    {
        var jwt = configuration.GetSection(JwtOptions.SectionName).Get<JwtOptions>() ?? new JwtOptions();
        services
            .AddAuthentication(JwtBearerDefaults.AuthenticationScheme)
            .AddJwtBearer(o =>
            {
                o.MapInboundClaims = false;
                o.TokenValidationParameters = new()
                {
                    ValidIssuer = jwt.Issuer,
                    ValidAudience = jwt.Audience,
                    IssuerSigningKey = jwt.CreateKey(),
                    NameClaimType = "unique_name",
                    RoleClaimType = "role",
                    ClockSkew = TimeSpan.FromSeconds(30),
                };
                // SignalR в браузере передаёт токен в query-параметре
                o.Events = new JwtBearerEvents
                {
                    OnMessageReceived = ctx =>
                    {
                        var token = ctx.Request.Query["access_token"];
                        if (!string.IsNullOrEmpty(token) && ctx.HttpContext.Request.Path.StartsWithSegments("/hubs"))
                        {
                            ctx.Token = token;
                        }

                        return Task.CompletedTask;
                    },
                };
            });

        // По умолчанию всё закрыто; открытые эндпоинты помечаются [AllowAnonymous]
        services.AddAuthorizationBuilder()
            .SetFallbackPolicy(new AuthorizationPolicyBuilder().RequireAuthenticatedUser().Build())
            .AddPolicy(Roles.Admin, p => p.RequireRole(Roles.Admin));
    }

    private static void AddRateLimiting(IServiceCollection services, IConfiguration configuration)
    {
        var permitPerMinute = configuration.GetValue("RateLimiting:PermitPerMinute", 600);
        services.AddRateLimiter(o =>
        {
            // Лимит на пользователя (по sub), для анонимных — на IP
            o.GlobalLimiter = PartitionedRateLimiter.Create<HttpContext, string>(ctx =>
                RateLimitPartition.GetFixedWindowLimiter(
                    ctx.User.FindFirst("sub")?.Value ?? ctx.Connection.RemoteIpAddress?.ToString() ?? "anonymous",
                    _ => new FixedWindowRateLimiterOptions
                    {
                        PermitLimit = permitPerMinute,
                        Window = TimeSpan.FromMinutes(1),
                        QueueLimit = 0,
                    }));
            o.RejectionStatusCode = StatusCodes.Status429TooManyRequests;
            o.OnRejected = async (ctx, ct) =>
            {
                if (ctx.Lease.TryGetMetadata(MetadataName.RetryAfter, out var retryAfter))
                {
                    ctx.HttpContext.Response.Headers.RetryAfter = ((int)Math.Ceiling(retryAfter.TotalSeconds)).ToString(System.Globalization.CultureInfo.InvariantCulture);
                }

                var problems = ctx.HttpContext.RequestServices.GetRequiredService<IProblemDetailsService>();
                await problems.WriteAsync(new ProblemDetailsContext
                {
                    HttpContext = ctx.HttpContext,
                    ProblemDetails = new ProblemDetails
                    {
                        Status = 429,
                        Title = AppExceptionHandler.TitleFor(429),
                        Detail = "Превышен лимит запросов.",
                        Extensions = { ["code"] = "rate_limited" },
                    },
                });
            };
        });
    }

    private static void AddHealth(IServiceCollection services, IConfiguration configuration)
    {
        var health = services.AddHealthChecks()
            .AddNpgSql(configuration.GetConnectionString(DependencyInjection.ConnectionStringName)!, name: "postgres", tags: ["ready"])
            .ForwardToPrometheus();

        var kafka = configuration.GetSection(KafkaOptions.SectionName).Get<KafkaOptions>() ?? new KafkaOptions();
        if (kafka.Enabled)
        {
            health.AddKafka(
                new ProducerConfig { BootstrapServers = kafka.BootstrapServers, MessageTimeoutMs = 5000 },
                topic: kafka.TopicPrefix + "healthcheck",
                name: "kafka",
                // Без Kafka API работает, события копятся в outbox — это снижение качества, а не отказ
                failureStatus: HealthStatus.Degraded,
                tags: ["ready"]);
        }
    }

    public static WebApplication MapApi(this WebApplication app)
    {
        app.MapControllers();
        app.MapHub<EventsHub>(EventsHub.Path);

        // live — процесс отвечает; ready — доступны БД и Kafka;
        // /health — полная модель здоровья: Healthy / Degraded (200) / Unhealthy (503)
        app.MapHealthChecks("/health/live", new HealthCheckOptions { Predicate = _ => false })
            .AllowAnonymous().DisableRateLimiting();
        app.MapHealthChecks("/health/ready", new HealthCheckOptions
        {
            Predicate = c => c.Tags.Contains("ready"),
            ResponseWriter = WriteHealthJson,
        }).AllowAnonymous().DisableRateLimiting();
        app.MapHealthChecks("/health", new HealthCheckOptions { ResponseWriter = WriteHealthJson })
            .AllowAnonymous().DisableRateLimiting();

        // Prometheus: HTTP-метрики, метрики .NET и Meter "App", статусы health checks.
        // Наружу не публикуется — доступ только из сети мониторинга (ограничивается на reverse proxy).
        app.MapMetrics("/metrics").AllowAnonymous().DisableRateLimiting();

        // OpenAPI по адресу, который ждут contract_diff и Swagger UI: /swagger/v1/swagger.json.
        // Включено в Development; на стенде — OpenApi__Enabled=true.
        if (IsOpenApiEnabled(app))
        {
            app.MapOpenApi("/swagger/{documentName}/swagger.json").AllowAnonymous().DisableRateLimiting();
        }

        return app;
    }

    private static bool IsOpenApiEnabled(WebApplication app) =>
        app.Configuration.GetValue("OpenApi:Enabled", app.Environment.IsDevelopment());

    /// <summary>Swagger UI на /swagger. Вызывается до аутентификации: статика UI не должна требовать токен.</summary>
    public static WebApplication UseApiDocs(this WebApplication app)
    {
        if (IsOpenApiEnabled(app))
        {
            app.UseSwaggerUI(o => o.SwaggerEndpoint("/swagger/v1/swagger.json", "v1"));
        }

        return app;
    }

    private static Task WriteHealthJson(HttpContext ctx, HealthReport report)
    {
        ctx.Response.ContentType = MediaTypeNames.Application.Json;
        return ctx.Response.WriteAsJsonAsync(new
        {
            status = report.Status.ToString(),
            totalMs = (int)report.TotalDuration.TotalMilliseconds,
            checks = report.Entries.Select(e => new
            {
                name = e.Key,
                status = e.Value.Status.ToString(),
                ms = (int)e.Value.Duration.TotalMilliseconds,
                description = e.Value.Description,
                error = e.Value.Exception?.Message,
            }),
        });
    }

    /// <summary>
    /// Режим "--healthcheck" для HEALTHCHECK в Dockerfile: в образе aspnet нет curl/wget.
    /// Возвращает 0, если /health/live отвечает 200.
    /// </summary>
    public static async Task<int> RunHealthcheckAsync()
    {
        var port = (Environment.GetEnvironmentVariable("ASPNETCORE_HTTP_PORTS") ?? "8080").Split(';', ',')[0];
        using var client = new HttpClient { Timeout = TimeSpan.FromSeconds(3) };
        try
        {
            using var response = await client.GetAsync(new Uri($"http://127.0.0.1:{port}/health/live"));
            return response.IsSuccessStatusCode ? 0 : 1;
        }
        catch (HttpRequestException)
        {
            return 1;
        }
        catch (TaskCanceledException)
        {
            return 1;
        }
    }
}
