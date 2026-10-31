using App.Infrastructure.Persistence;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;

namespace App.IntegrationTests;

/// <summary>
/// Шлюз согласованности EF-модели и схемы БД: каждая сущность модели должна читаться реальным запросом.
/// Ловит расхождения имён таблиц/колонок и типов между сгенерированными сущностями и DDL.
/// </summary>
public sealed class PersistenceModelTests(AppFixture fixture)
{
    [Fact]
    public async Task EveryEntity_IsQueryable_AgainstRealSchema()
    {
        await using var scope = fixture.Factory.Services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        var failures = new List<string>();

        foreach (var entityType in db.Model.GetEntityTypes().Where(e => !e.IsOwned()))
        {
            var set = typeof(PersistenceModelTests)
                .GetMethod(nameof(FirstOrDefaultAsync), System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static)!
                .MakeGenericMethod(entityType.ClrType);
            try
            {
                await (Task)set.Invoke(null, [db, TestContext.Current.CancellationToken])!;
            }
            catch (Exception ex) when (ex is DbUpdateException or Npgsql.PostgresException or InvalidOperationException or InvalidCastException)
            {
                failures.Add($"{entityType.ClrType.Name}: {ex.GetBaseException().Message}");
            }
        }

        Assert.Empty(failures);
    }

    private static async Task FirstOrDefaultAsync<T>(AppDbContext db, CancellationToken ct)
        where T : class => await db.Set<T>().AsNoTracking().FirstOrDefaultAsync(ct);
}
