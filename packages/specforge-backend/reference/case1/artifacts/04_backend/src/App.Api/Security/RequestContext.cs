using App.Api.Idempotency;
using App.Application.Abstractions;

namespace App.Api.Security;

internal sealed class RequestContext(IHttpContextAccessor accessor) : IRequestContext
{
    public string? IdempotencyKey =>
        accessor.HttpContext?.Request.Headers.TryGetValue(IdempotencyFilter.HeaderName, out var key) == true ? key.ToString() : null;
}
