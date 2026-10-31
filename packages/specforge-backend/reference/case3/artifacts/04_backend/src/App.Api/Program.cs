using App.Api;
using App.Application;
using App.Infrastructure;
using Prometheus;

if (args.Contains("--healthcheck"))
{
    return await ApiSetup.RunHealthcheckAsync();
}

var builder = WebApplication.CreateBuilder(args);

if (!builder.Environment.IsDevelopment())
{
    builder.Logging.ClearProviders().AddJsonConsole(o => o.UseUtcTimestamp = true);
}

builder.Services
    .AddApplication()
    .AddInfrastructure(builder.Configuration)
    .AddApi(builder.Configuration);

var app = builder.Build();

app.UseForwardedHeaders();
app.Use((ctx, next) =>
{
    // Сквозной идентификатор запроса: X-Request-Id (nginx админки) или X-Correlation-Id (спецификация API).
    // Становится идентификатором запроса в логах и возвращается клиенту в том же заголовке.
    foreach (var header in (string[])["X-Request-Id", "X-Correlation-Id"])
    {
        if (ctx.Request.Headers.TryGetValue(header, out var requestId) && !string.IsNullOrWhiteSpace(requestId))
        {
            ctx.TraceIdentifier = requestId.ToString();
            ctx.Response.Headers[header] = requestId;
            break;
        }
    }

    return next(ctx);
});
app.UseExceptionHandler();
app.UseStatusCodePages(); // пустые 401/403/404/405 → problem+json
app.Use((ctx, next) =>
{
    // Тело запроса читается повторно фильтром идемпотентности
    ctx.Request.EnableBuffering();
    return next(ctx);
});
app.UseHttpMetrics();
app.UseApiDocs();
app.UseCors(ApiSetup.CorsPolicy);
app.UseAuthentication();
app.UseRateLimiter();
app.UseAuthorization();
app.MapApi();

await app.RunAsync();
return 0;

/// <summary>Точка входа; partial нужен для WebApplicationFactory в интеграционных тестах.</summary>
public partial class Program;
