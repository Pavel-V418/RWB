using App.Application.Abstractions;
using App.Application.Common;
using Microsoft.IdentityModel.JsonWebTokens;

namespace App.Api.Security;

internal sealed class CurrentUser(IHttpContextAccessor accessor) : ICurrentUser
{
    public bool IsAuthenticated => accessor.HttpContext?.User.Identity?.IsAuthenticated == true;

    public Guid? UserId =>
        Guid.TryParse(accessor.HttpContext?.User.FindFirst(JwtRegisteredClaimNames.Sub)?.Value, out var id) ? id : null;

    public string? Role => accessor.HttpContext?.User.FindFirst("role")?.Value;

    public bool IsInRole(string role) => string.Equals(Role, role, StringComparison.Ordinal);
}

/// <summary>По умолчанию на ключи подписываются только администраторы. Backend-агент заменяет доменной реализацией.</summary>
internal sealed class AdminOnlySubscriptionGuard : ISubscriptionGuard
{
    public Task<bool> CanSubscribeAsync(ICurrentUser user, string subscriptionKey, CancellationToken cancellationToken) =>
        Task.FromResult(user.IsInRole(Roles.Admin));
}
