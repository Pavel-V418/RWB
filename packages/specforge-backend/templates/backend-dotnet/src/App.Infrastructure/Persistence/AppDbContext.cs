using App.Application.Abstractions;
using App.Infrastructure.Idempotency;
using App.Infrastructure.Messaging;
using Microsoft.EntityFrameworkCore;

namespace App.Infrastructure.Persistence;

/// <summary>
/// Контекст БД. Схема БД создаётся из SQL (db/*.sql + доменный DDL), миграции EF не используются.
/// Генератор EF-моделей дописывает DbSet-ы и конфигурации в <c>AppDbContext.Generated.cs</c>
/// через partial-метод <see cref="OnModelCreatingGenerated"/>.
/// </summary>
public partial class AppDbContext(DbContextOptions<AppDbContext> options, OutboxSignal outboxSignal)
    : DbContext(options), IAppDbContext
{
    public DbSet<OutboxMessage> OutboxMessages => Set<OutboxMessage>();

    public DbSet<IdempotencyRecord> IdempotencyRecords => Set<IdempotencyRecord>();

    public override async Task<int> SaveChangesAsync(CancellationToken cancellationToken = default)
    {
        var hasNewEvents = ChangeTracker.Entries<OutboxMessage>().Any(e => e.State == EntityState.Added);
        var result = await base.SaveChangesAsync(cancellationToken);
        if (hasNewEvents)
        {
            // Будим публикатор сразу, не дожидаясь следующего опроса
            outboxSignal.Notify();
        }

        return result;
    }

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        ArgumentNullException.ThrowIfNull(modelBuilder);
        modelBuilder.HasPostgresExtension("postgis");
        modelBuilder.ApplyConfigurationsFromAssembly(typeof(AppDbContext).Assembly);
        OnModelCreatingGenerated(modelBuilder);
    }

    partial void OnModelCreatingGenerated(ModelBuilder modelBuilder);
}
