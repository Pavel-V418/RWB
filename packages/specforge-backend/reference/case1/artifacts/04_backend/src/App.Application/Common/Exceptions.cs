namespace App.Application.Common;

/// <summary>
/// Базовое исключение прикладного слоя. Глобальный обработчик API превращает его
/// в ответ RFC 9457 с <see cref="StatusCode"/> и <see cref="Code"/>.
/// </summary>
public abstract class AppException : Exception
{
    protected AppException(int statusCode, string code, string message)
        : base(message)
    {
        StatusCode = statusCode;
        Code = code;
    }

    public int StatusCode { get; }

    public string Code { get; }
}

/// <summary>404: ресурс не найден.</summary>
public sealed class NotFoundException(string resource, object id)
    : AppException(404, "not_found", $"{resource} '{id}' не найден.");

/// <summary>
/// 400: ошибка входных данных, которую не выразить атрибутами DTO (например, время из будущего).
/// Ответ — как у ошибок валидации модели: <c>errors</c> { "Points[0].RecordedAt": ["..."] }, code validation_failed.
/// </summary>
public sealed class RequestValidationException(IReadOnlyDictionary<string, string[]> errors)
    : AppException(400, "validation_failed", "Запрос не прошёл проверку.")
{
    public RequestValidationException(string field, string message)
        : this(new Dictionary<string, string[]> { [field] = [message] })
    {
    }

    public IReadOnlyDictionary<string, string[]> Errors { get; } = errors;
}

/// <summary>409: конфликт состояния (уже существует, уже активно и т. п.).</summary>
public sealed class ConflictException(string code, string message)
    : AppException(409, code, message);

/// <summary>403: у пользователя нет доступа к ресурсу.</summary>
public sealed class ForbiddenException(string message = "Недостаточно прав.")
    : AppException(403, "forbidden", message);

/// <summary>422: запрос корректен по формату, но нарушает бизнес-правило.</summary>
public sealed class BusinessRuleException(string code, string message)
    : AppException(422, code, message);

/// <summary>502: внешний провайдер недоступен или вернул ошибку.</summary>
public sealed class ExternalProviderException(string provider, string message)
    : AppException(502, "external_provider_error", $"{provider}: {message}");

/// <summary>401: неверные учётные данные (для протухшего токена 401 отдаёт сам JWT-middleware).</summary>
public sealed class UnauthorizedException(string message = "Неверный логин или пароль.")
    : AppException(401, "invalid_credentials", message);

/// <summary>501: операция объявлена в контракте, но ещё не реализована (заглушка backend-агента).</summary>
public sealed class FeatureNotImplementedException(string operation)
    : AppException(501, "not_implemented", $"Операция {operation} пока не реализована.");
