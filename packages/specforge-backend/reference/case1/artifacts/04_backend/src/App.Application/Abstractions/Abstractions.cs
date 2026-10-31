using App.Domain.Common;
using Microsoft.EntityFrameworkCore;

namespace App.Application.Abstractions;

/// <summary>
/// Контекст БД для прикладного слоя. Генератор EF-моделей дописывает DbSet-ы
/// в partial-класс реализации и в partial-интерфейс <c>IAppDbContext.Generated.cs</c>.
/// </summary>
public partial interface IAppDbContext
{
    DbSet<TEntity> Set<TEntity>()
        where TEntity : class;

    Task<int> SaveChangesAsync(CancellationToken cancellationToken = default);
}

/// <summary>
/// Transactional outbox: событие сохраняется в той же транзакции, что и данные,
/// и публикуется в Kafka и SignalR фоновым сервисом после <c>SaveChangesAsync</c>.
/// </summary>
public interface IOutbox
{
    void Enqueue(IIntegrationEvent integrationEvent);
}

/// <summary>Отправка сообщений в реальном времени (реализация — SignalR-хаб /hubs/events).</summary>
public interface IRealtimeNotifier
{
    /// <param name="method">Имя клиентского метода, обычно тип события.</param>
    /// <param name="payload">Сериализуемый объект.</param>
    /// <param name="subscriptionKey">Если задан, сообщение уходит также подписчикам этого ключа (например, id автомобиля).</param>
    /// <param name="cancellationToken">Токен отмены.</param>
    Task PublishAsync(string method, object payload, string? subscriptionKey, CancellationToken cancellationToken);
}

/// <summary>
/// Кто может подписаться на realtime-сообщения по ключу. Реализация по умолчанию:
/// admin — на любой ключ, остальные — запрещено. Backend-агент заменяет её доменной.
/// </summary>
public interface ISubscriptionGuard
{
    Task<bool> CanSubscribeAsync(ICurrentUser user, string subscriptionKey, CancellationToken cancellationToken);
}

/// <summary>Текущий пользователь из JWT.</summary>
public interface ICurrentUser
{
    bool IsAuthenticated { get; }

    Guid? UserId { get; }

    string? Role { get; }

    bool IsInRole(string role);
}

/// <summary>Выпуск JWT.</summary>
public interface ITokenService
{
    AccessToken Issue(Guid userId, string login, string role);
}

public sealed record AccessToken(string Token, int ExpiresInSeconds);

/// <summary>Хеширование паролей (PBKDF2).</summary>
public interface IPasswordHasher
{
    string Hash(string password);

    bool Verify(string password, string hash);
}

/// <summary>Данные текущего HTTP-запроса, нужные прикладному слою.</summary>
public interface IRequestContext
{
    /// <summary>
    /// Заголовок Idempotency-Key запроса (для операций с x-idempotent). Передавай его внешнему провайдеру,
    /// чтобы повтор запроса не создал у провайдера вторую операцию (например, оплату).
    /// </summary>
    string? IdempotencyKey { get; }
}

/// <summary>
/// Периодическая фоновая задача (таймеры предметной области: предупреждение об окончании оплаты, истечение и т. п.).
/// Хост вызывает <see cref="RunAsync"/> каждые <see cref="Interval"/> по <see cref="TimeProvider"/> (TIME_SCALE ускоряет и его)
/// в отдельном DI-scope. Реализация должна быть идемпотентной: условие отбора исключает уже обработанные строки.
/// Регистрация: <c>services.AddScoped&lt;IScheduledJob, MyJob&gt;()</c>.
/// </summary>
public interface IScheduledJob
{
    /// <summary>Имя для логов, метрик и здоровья, например "session-expiry".</summary>
    string Name { get; }

    /// <summary>Период запуска во времени предметной области (при TIME_SCALE=60 одна минута — секунда реального времени).</summary>
    TimeSpan Interval { get; }

    /// <summary>Один проход. Возвращает число обработанных объектов (для логов и метрик).</summary>
    Task<int> RunAsync(CancellationToken cancellationToken);
}
