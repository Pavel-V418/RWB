using App.Application.Abstractions;
using App.Infrastructure.Jobs;
using App.Infrastructure.Messaging;
using App.Infrastructure.Observability;
using App.Infrastructure.Persistence;
using App.Infrastructure.Security;
using App.Infrastructure.Time;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;

namespace App.Infrastructure;

public static partial class DependencyInjection
{
    public const string ConnectionStringName = "Database";

    public static IServiceCollection AddInfrastructure(this IServiceCollection services, IConfiguration configuration)
    {
        var connectionString = configuration.GetConnectionString(ConnectionStringName)
            ?? throw new InvalidOperationException($"Не задана строка подключения ConnectionStrings:{ConnectionStringName}.");

        // TIME_SCALE (или Time:Scale) > 1 ускоряет время и таймеры — для демо и e2e
        var timeScale = configuration.GetValue<double?>("TIME_SCALE") ?? configuration.GetValue("Time:Scale", 1.0);
        services.TryAddSingleton<TimeProvider>(Math.Abs(timeScale - 1.0) < double.Epsilon
            ? TimeProvider.System
            : new ScaledTimeProvider(TimeProvider.System, timeScale));
        services.AddSingleton<OutboxSignal>();
        services.AddMetrics();
        services.AddSingleton<AppMetrics>();
        services.AddDbContext<AppDbContext>(o => o
            .UseNpgsql(connectionString, npgsql => npgsql.UseNetTopologySuite())
            .UseSnakeCaseNamingConvention());
        services.AddScoped<IAppDbContext>(sp => sp.GetRequiredService<AppDbContext>());

        services.Configure<KafkaOptions>(configuration.GetSection(KafkaOptions.SectionName));
        var kafka = configuration.GetSection(KafkaOptions.SectionName).Get<KafkaOptions>() ?? new KafkaOptions();
        if (kafka.Enabled)
        {
            services.TryAddSingleton<IMessageBus, KafkaMessageBus>();
        }
        else
        {
            services.TryAddSingleton<IMessageBus, NoOpMessageBus>();
        }

        services.AddScoped<IOutbox, Outbox>();
        services.AddHostedService<OutboxPublisher>();
        services.AddHealthChecks().AddCheck<OutboxHealthCheck>("outbox", tags: ["business"]);

        // Таймеры предметной области: все IScheduledJob из Application
        services.AddSingleton<ScheduledJobsState>();
        services.AddHostedService<ScheduledJobsHost>();
        services.AddHealthChecks().AddCheck<ScheduledJobsHealthCheck>("jobs", tags: ["business"]);

        services.Configure<JwtOptions>(configuration.GetSection(JwtOptions.SectionName));
        // Токены — по реальным часам: при TIME_SCALE проверка JWT (nbf/exp) идёт по настоящему времени
        services.AddSingleton<ITokenService>(sp => new JwtTokenService(sp.GetRequiredService<Microsoft.Extensions.Options.IOptions<JwtOptions>>(), TimeProvider.System));
        services.AddSingleton<IPasswordHasher, Pbkdf2PasswordHasher>();

        AddInfrastructureServices(services, configuration);
        return services;
    }

    /// <summary>Точка расширения для адаптеров внешних провайдеров и доменных реализаций.</summary>
    static partial void AddInfrastructureServices(IServiceCollection services, IConfiguration configuration);
}
