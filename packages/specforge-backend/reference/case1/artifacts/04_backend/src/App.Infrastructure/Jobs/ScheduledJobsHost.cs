using System.Collections.Concurrent;
using System.Diagnostics.Metrics;
using App.Application.Abstractions;
using App.Infrastructure.Observability;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Diagnostics.HealthChecks;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace App.Infrastructure.Jobs;

/// <summary>Состояние фоновых задач для здоровья и метрик.</summary>
public sealed class ScheduledJobsState
{
    internal ConcurrentDictionary<string, (int ConsecutiveFailures, DateTimeOffset? LastSuccess)> Jobs { get; } = new();
}

/// <summary>
/// Запускает все зарегистрированные <see cref="IScheduledJob"/>: у каждой свой цикл с PeriodicTimer на TimeProvider,
/// каждый проход — в новом DI-scope, сбой одного прохода не останавливает задачу и не мешает другим.
/// </summary>
internal sealed partial class ScheduledJobsHost(
    IServiceScopeFactory scopeFactory,
    TimeProvider time,
    ScheduledJobsState state,
    IMeterFactory meterFactory,
    ILogger<ScheduledJobsHost> logger) : BackgroundService
{
    private readonly Counter<long> _runs = meterFactory.Create(AppMetrics.MeterName).CreateCounter<long>("app_job_runs", "{run}", "Проходы фоновых задач");
    private readonly Counter<long> _failures = meterFactory.Create(AppMetrics.MeterName).CreateCounter<long>("app_job_failures", "{run}", "Упавшие проходы фоновых задач");

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        List<(Type Type, string Name, TimeSpan Interval)> jobs;
        await using (var scope = scopeFactory.CreateAsyncScope())
        {
            jobs = scope.ServiceProvider.GetServices<IScheduledJob>().Select(j => (j.GetType(), j.Name, j.Interval)).ToList();
        }

        foreach (var job in jobs)
        {
            state.Jobs[job.Name] = (0, null);
            LogJobScheduled(logger, job.Name, job.Interval);
        }

        await Task.WhenAll(jobs.Select(j => LoopAsync(j.Type, j.Name, j.Interval, stoppingToken)));
    }

    private async Task LoopAsync(Type type, string name, TimeSpan interval, CancellationToken stoppingToken)
    {
        using var timer = new PeriodicTimer(interval, time);
        try
        {
            while (await timer.WaitForNextTickAsync(stoppingToken))
            {
                await RunOnceAsync(type, name, stoppingToken);
            }
        }
        catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
        {
            // остановка сервиса
        }
    }

    internal async Task RunOnceAsync(Type type, string name, CancellationToken stoppingToken)
    {
        var tag = new KeyValuePair<string, object?>("job", name);
        try
        {
            await using var scope = scopeFactory.CreateAsyncScope();
            var job = scope.ServiceProvider.GetServices<IScheduledJob>().First(j => j.GetType() == type);
            var processed = await job.RunAsync(stoppingToken);
            state.Jobs[name] = (0, time.GetUtcNow());
            _runs.Add(1, tag);
            if (processed > 0)
            {
                LogJobProcessed(logger, name, processed);
            }
        }
        catch (Exception ex) when (ex is not OperationCanceledException || !stoppingToken.IsCancellationRequested)
        {
            var (failures, lastSuccess) = state.Jobs.GetValueOrDefault(name);
            state.Jobs[name] = (failures + 1, lastSuccess);
            _failures.Add(1, tag);
            LogJobFailed(logger, name, ex);
        }
    }

    [LoggerMessage(Level = LogLevel.Information, Message = "Фоновая задача {Job}: период {Interval}")]
    private static partial void LogJobScheduled(ILogger logger, string job, TimeSpan interval);

    [LoggerMessage(Level = LogLevel.Information, Message = "Фоновая задача {Job}: обработано {Count}")]
    private static partial void LogJobProcessed(ILogger logger, string job, int count);

    [LoggerMessage(Level = LogLevel.Error, Message = "Фоновая задача {Job}: проход упал")]
    private static partial void LogJobFailed(ILogger logger, string job, Exception ex);
}

/// <summary>Degraded, если какая-то задача падает 3 прохода подряд: API работает, но таймеры предметной области — нет.</summary>
internal sealed class ScheduledJobsHealthCheck(ScheduledJobsState state) : IHealthCheck
{
    public Task<HealthCheckResult> CheckHealthAsync(HealthCheckContext context, CancellationToken cancellationToken = default)
    {
        var failing = state.Jobs.Where(j => j.Value.ConsecutiveFailures >= 3).Select(j => j.Key).ToList();
        var data = state.Jobs.ToDictionary(j => j.Key, j => (object)(j.Value.LastSuccess?.ToString("O") ?? "ещё не выполнялась"));
        return Task.FromResult(failing.Count > 0
            ? HealthCheckResult.Degraded($"Фоновые задачи падают: {string.Join(", ", failing)}", data: data)
            : HealthCheckResult.Healthy($"Фоновых задач: {state.Jobs.Count}", data));
    }
}
