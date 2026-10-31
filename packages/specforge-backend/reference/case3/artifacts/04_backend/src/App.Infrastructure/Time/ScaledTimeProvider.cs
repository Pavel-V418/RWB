namespace App.Infrastructure.Time;

/// <summary>
/// Ускоренное время для демо и e2e (<c>TIME_SCALE=10</c> — 1 реальная секунда = 10 секундам системы).
/// Масштабирует и текущее время, и таймеры: <c>Task.Delay(…, TimeProvider)</c>, <c>PeriodicTimer</c>.
/// Поэтому доменный код обязан брать время только из <see cref="TimeProvider"/>, а не из <c>DateTime.UtcNow</c>.
/// </summary>
public sealed class ScaledTimeProvider : TimeProvider
{
    private readonly TimeProvider _inner;
    private readonly DateTimeOffset _origin;

    public ScaledTimeProvider(TimeProvider inner, double scale)
    {
        ArgumentNullException.ThrowIfNull(inner);
        ArgumentOutOfRangeException.ThrowIfNegativeOrZero(scale);
        _inner = inner;
        Scale = scale;
        _origin = inner.GetUtcNow();
    }

    public double Scale { get; }

    public override TimeZoneInfo LocalTimeZone => _inner.LocalTimeZone;

    public override long TimestampFrequency => Math.Max(1, (long)(_inner.TimestampFrequency / Scale));

    public override long GetTimestamp() => _inner.GetTimestamp();

    public override DateTimeOffset GetUtcNow() => _origin + ((_inner.GetUtcNow() - _origin) * Scale);

    public override ITimer CreateTimer(TimerCallback callback, object? state, TimeSpan dueTime, TimeSpan period) =>
        new ScaledTimer(_inner.CreateTimer(callback, state, ToReal(dueTime), ToReal(period)), this);

    private TimeSpan ToReal(TimeSpan span) => span == Timeout.InfiniteTimeSpan ? span : span / Scale;

    private sealed class ScaledTimer(ITimer inner, ScaledTimeProvider owner) : ITimer
    {
        public bool Change(TimeSpan dueTime, TimeSpan period) => inner.Change(owner.ToReal(dueTime), owner.ToReal(period));

        public void Dispose() => inner.Dispose();

        public ValueTask DisposeAsync() => inner.DisposeAsync();
    }
}
