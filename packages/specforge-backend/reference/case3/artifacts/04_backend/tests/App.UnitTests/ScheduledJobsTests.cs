using App.Application.Abstractions;
using App.Infrastructure.Jobs;
using App.Infrastructure.Time;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Diagnostics.HealthChecks;
using Microsoft.Extensions.Logging.Abstractions;

namespace App.UnitTests;

public sealed class ScheduledJobsTests
{
    [Fact]
    public async Task Jobs_RunOnScaledSchedule_AndFailingJobMakesHealthDegraded()
    {
        var services = new ServiceCollection();
        services.AddMetrics();
        services.AddSingleton<TimeProvider>(new ScaledTimeProvider(TimeProvider.System, 600)); // 1 мин = 100 мс
        services.AddSingleton<ScheduledJobsState>();
        services.AddSingleton<Counter>();
        services.AddScoped<IScheduledJob, CountingJob>();
        services.AddScoped<IScheduledJob, FailingJob>();
        await using var provider = services.BuildServiceProvider();
        var host = ActivatorUtilities.CreateInstance<ScheduledJobsHost>(provider, NullLogger<ScheduledJobsHost>.Instance);

        await host.StartAsync(TestContext.Current.CancellationToken);
        await Task.Delay(TimeSpan.FromMilliseconds(800), TestContext.Current.CancellationToken);
        await host.StopAsync(TestContext.Current.CancellationToken);

        Assert.InRange(provider.GetRequiredService<Counter>().Value, 3, 20);
        var health = await new ScheduledJobsHealthCheck(provider.GetRequiredService<ScheduledJobsState>())
            .CheckHealthAsync(new HealthCheckContext(), TestContext.Current.CancellationToken);
        Assert.Equal(HealthStatus.Degraded, health.Status);
        Assert.Contains("failing", health.Description, StringComparison.Ordinal);
    }

    private sealed class Counter
    {
        public int Value;
    }

    private sealed class CountingJob(Counter counter) : IScheduledJob
    {
        public string Name => "counting";

        public TimeSpan Interval => TimeSpan.FromMinutes(1);

        public Task<int> RunAsync(CancellationToken cancellationToken) => Task.FromResult(Interlocked.Increment(ref counter.Value));
    }

    private sealed class FailingJob : IScheduledJob
    {
        public string Name => "failing";

        public TimeSpan Interval => TimeSpan.FromMinutes(1);

        public Task<int> RunAsync(CancellationToken cancellationToken) => throw new InvalidOperationException("сбой");
    }
}
