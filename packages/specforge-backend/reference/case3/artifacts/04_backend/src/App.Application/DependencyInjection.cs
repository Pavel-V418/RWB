using Microsoft.Extensions.DependencyInjection;

namespace App.Application;

public static partial class DependencyInjection
{
    /// <summary>
    /// Регистрирует сервисы прикладного слоя. Backend-агент добавляет свои сервисы
    /// в <c>AddApplicationServices</c> (partial-метод в отдельном файле).
    /// </summary>
    public static IServiceCollection AddApplication(this IServiceCollection services)
    {
        AddApplicationServices(services);
        return services;
    }

    static partial void AddApplicationServices(IServiceCollection services);
}
