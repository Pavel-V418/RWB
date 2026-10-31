using App.Application.Common;
using App.Domain.Common;
using Microsoft.AspNetCore.Diagnostics;
using Microsoft.AspNetCore.Mvc;

namespace App.Api.Errors;

/// <summary>
/// Превращает исключения в ответы RFC 9457 (application/problem+json) с полем "code".
/// Неизвестные исключения → 500 без деталей реализации.
/// </summary>
internal sealed partial class AppExceptionHandler(IProblemDetailsService problemDetails, ILogger<AppExceptionHandler> logger)
    : IExceptionHandler
{
    public async ValueTask<bool> TryHandleAsync(HttpContext httpContext, Exception exception, CancellationToken cancellationToken)
    {
        var (status, code, title, detail) = exception switch
        {
            AppException e => (e.StatusCode, e.Code, TitleFor(e.StatusCode), e.Message),
            DomainException e => (StatusCodes.Status422UnprocessableEntity, e.Code, TitleFor(422), e.Message),
            BadHttpRequestException e => (e.StatusCode, "bad_request", TitleFor(e.StatusCode), e.Message),
            _ => (StatusCodes.Status500InternalServerError, "internal_error", TitleFor(500), "Внутренняя ошибка сервера."),
        };

        if (status >= 500)
        {
            LogUnhandled(logger, exception);
        }

        var problem = new ProblemDetails
        {
            Status = status,
            Title = title,
            Detail = detail,
            Type = $"https://errors.app.local/{code.Replace('_', '-')}",
            Extensions = { ["code"] = code },
        };
        if (exception is RequestValidationException validation)
        {
            problem.Extensions["errors"] = validation.Errors;
        }

        httpContext.Response.StatusCode = status;
        return await problemDetails.TryWriteAsync(new ProblemDetailsContext
        {
            HttpContext = httpContext,
            Exception = exception,
            ProblemDetails = problem,
        });
    }

    internal static string TitleFor(int status) => status switch
    {
        400 => "Некорректный запрос",
        401 => "Требуется аутентификация",
        403 => "Доступ запрещён",
        404 => "Не найдено",
        409 => "Конфликт",
        422 => "Нарушено бизнес-правило",
        429 => "Слишком много запросов",
        502 => "Ошибка внешнего сервиса",
        501 => "Не реализовано",
        _ => "Ошибка сервера",
    };

    [LoggerMessage(Level = LogLevel.Error, Message = "Необработанное исключение")]
    private static partial void LogUnhandled(ILogger logger, Exception ex);
}
