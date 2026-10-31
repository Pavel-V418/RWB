using App.Application.Abstractions;
using App.Application.Common;
using App.Application.Contracts;
using App.Domain.Entities;
using Microsoft.EntityFrameworkCore;

namespace App.Application.Features.SystemSettings;

/// <summary>
/// Реализация операций с настройками системы (FR-7).
/// </summary>
internal sealed class SystemSettingsApi(
    IAppDbContext db,
    ICurrentUser currentUser,
    TimeProvider time) : ISystemSettingsApi
{
    public async Task<IReadOnlyList<SystemSettingDto>> GetSystemSettingsAsync(CancellationToken cancellationToken)
    {
        var settings = await db.SystemSettings
            .AsNoTracking()
            .OrderBy(s => s.Key)
            .ToListAsync(cancellationToken);

        return settings.Select(ToDto).ToList();
    }

    public async Task<SystemSettingDto> UpdateSystemSettingAsync(string key, UpdateSystemSettingRequestDto body, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(body);

        if (string.IsNullOrWhiteSpace(body.Value))
        {
            throw new RequestValidationException("Value", "Значение настройки не может быть пустым.");
        }

        if (body.Value.Length > 500)
        {
            throw new RequestValidationException("Value", "Значение настройки не может превышать 500 символов.");
        }

        var setting = await db.SystemSettings
            .FirstOrDefaultAsync(s => s.Key == key, cancellationToken)
            ?? throw new NotFoundException("Настройка системы", key);

        var now = time.GetUtcNow();
        setting.Value = body.Value;
        setting.UpdatedAt = now;

        var auditLog = new AuditLog
        {
            Id = Guid.NewGuid(),
            UserId = currentUser.UserId,
            Action = "UpdateSystemSetting",
            EntityType = "SystemSetting",
            EntityId = setting.Id,
            Timestamp = now,
            Details = $"Key: {key}, NewValue: {body.Value}",
            CreatedAt = now,
            UpdatedAt = now
        };

        db.AuditLogs.Add(auditLog);
        await db.SaveChangesAsync(cancellationToken);

        return ToDto(setting);
    }

    private static SystemSettingDto ToDto(SystemSetting entity) => new()
    {
        Id = entity.Id,
        Key = entity.Key,
        Value = entity.Value,
        CreatedAt = entity.CreatedAt,
        UpdatedAt = entity.UpdatedAt
    };
}
