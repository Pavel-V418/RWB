using App.Infrastructure.Time;

namespace App.UnitTests;

public sealed class ScaledTimeProviderTests
{
    [Fact]
    public async Task Clock_And_Timers_RunFaster_ByScale()
    {
        var time = new ScaledTimeProvider(TimeProvider.System, 600);
        var start = time.GetUtcNow();
        var watch = System.Diagnostics.Stopwatch.StartNew();

        // 1 минута системного времени при масштабе 600 = 100 мс реального
        await Task.Delay(TimeSpan.FromMinutes(1), time, TestContext.Current.CancellationToken);

        Assert.InRange(watch.ElapsedMilliseconds, 50, 2000);
        Assert.True(time.GetUtcNow() - start >= TimeSpan.FromSeconds(55));
    }

    [Fact]
    public void Scale_MustBePositive() =>
        Assert.Throws<ArgumentOutOfRangeException>(() => new ScaledTimeProvider(TimeProvider.System, 0));
}
