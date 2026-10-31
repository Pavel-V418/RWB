using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using App.Application.Abstractions;
using App.Infrastructure.Idempotency;
using App.Infrastructure.Persistence;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.Filters;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Options;

namespace App.Api.Idempotency;

/// <summary>
/// Помечает критичную операцию: требуется заголовок Idempotency-Key.
/// Повтор с тем же ключом и тем же запросом возвращает сохранённый ответ,
/// тот же ключ с другим запросом → 422 idempotency_key_reused.
/// </summary>
[AttributeUsage(AttributeTargets.Method)]
public sealed class IdempotentAttribute : TypeFilterAttribute
{
    public IdempotentAttribute()
        : base(typeof(IdempotencyFilter))
    {
    }
}

internal sealed partial class IdempotencyFilter(
    AppDbContext db,
    ICurrentUser currentUser,
    TimeProvider clock,
    IOptions<JsonOptions> jsonOptions,
    ILogger<IdempotencyFilter> logger) : IAsyncActionFilter
{
    public const string HeaderName = "Idempotency-Key";

    public async Task OnActionExecutionAsync(ActionExecutingContext context, ActionExecutionDelegate next)
    {
        var http = context.HttpContext;
        var key = http.Request.Headers[HeaderName].ToString();
        if (!IsValidKey(key))
        {
            context.Result = Problem(400, "idempotency_key_required",
                $"Заголовок {HeaderName} обязателен: 8–64 символа [A-Za-z0-9_-].");
            return;
        }

        var userId = currentUser.UserId ?? Guid.Empty;
        var requestHash = await ComputeRequestHashAsync(http.Request);
        var ct = http.RequestAborted;

        var existing = await db.IdempotencyRecords.AsNoTracking()
            .FirstOrDefaultAsync(r => r.UserId == userId && r.Key == key, ct);
        if (existing is not null)
        {
            context.Result = existing.RequestHash == requestHash
                ? new ContentResult
                {
                    StatusCode = existing.ResponseStatus,
                    Content = existing.ResponseBody,
                    ContentType = existing.ResponseContentType,
                }
                : Problem(422, "idempotency_key_reused", "Ключ идемпотентности уже использован для другого запроса.");
            return;
        }

        var executed = await next();
        if (executed.Exception is not null || executed.Result is not ObjectResult { StatusCode: >= 200 and < 300 } result)
        {
            return; // ошибки не запоминаем — клиент может повторить
        }

        db.IdempotencyRecords.Add(new IdempotencyRecord
        {
            UserId = userId,
            Key = key,
            RequestHash = requestHash,
            ResponseStatus = (short)result.StatusCode!.Value,
            ResponseBody = result.Value is null ? null : JsonSerializer.Serialize(result.Value, jsonOptions.Value.JsonSerializerOptions),
            ResponseContentType = "application/json; charset=utf-8",
            CreatedAt = clock.GetUtcNow(),
        });
        try
        {
            await db.SaveChangesAsync(ct);
        }
        catch (DbUpdateException ex)
        {
            // Параллельный запрос с тем же ключом успел первым — ответ уже сохранён
            LogConcurrentKey(logger, key, ex);
        }
    }

    private static bool IsValidKey(string key) =>
        key.Length is >= 8 and <= 64 && key.All(c => char.IsAsciiLetterOrDigit(c) || c is '_' or '-');

    private static async Task<string> ComputeRequestHashAsync(HttpRequest request)
    {
        request.Body.Position = 0;
        using var reader = new StreamReader(request.Body, Encoding.UTF8, leaveOpen: true);
        var body = await reader.ReadToEndAsync(request.HttpContext.RequestAborted);
        request.Body.Position = 0;
        var bytes = Encoding.UTF8.GetBytes($"{request.Method}\n{request.Path}\n{body}");
        return Convert.ToHexStringLower(SHA256.HashData(bytes));
    }

    private static ObjectResult Problem(int status, string code, string detail) => new(new ProblemDetails
    {
        Status = status,
        Title = Errors.AppExceptionHandler.TitleFor(status),
        Detail = detail,
        Type = $"https://errors.app.local/{code.Replace('_', '-')}",
        Extensions = { ["code"] = code },
    })
    {
        StatusCode = status,
        ContentTypes = { "application/problem+json" },
    };

    [LoggerMessage(Level = LogLevel.Information, Message = "Idempotency-Key {Key}: параллельный запрос")]
    private static partial void LogConcurrentKey(ILogger logger, string key, Exception ex);
}
