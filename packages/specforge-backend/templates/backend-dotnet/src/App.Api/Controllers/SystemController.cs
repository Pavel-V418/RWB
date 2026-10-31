using System.Reflection;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace App.Api.Controllers;

/// <summary>
/// Образец соглашений для генерируемых контроллеров: маршрут /api/v1/..., [ApiController],
/// явные коды ответов. Доменные контроллеры генерируются из OpenAPI.
/// </summary>
[ApiController]
[Route("api/v1/system")]
public sealed partial class SystemController(
    IHostEnvironment environment,
    TimeProvider clock,
    IHostApplicationLifetime lifetime,
    ILogger<SystemController> logger) : ControllerBase
{
    [HttpGet("info")]
    [AllowAnonymous]
    [ProducesResponseType<SystemInfo>(StatusCodes.Status200OK)]
    public ActionResult<SystemInfo> GetInfo() => new SystemInfo(
        environment.ApplicationName,
        Assembly.GetEntryAssembly()?.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion ?? "dev",
        environment.EnvironmentName,
        clock.GetUtcNow());

    /// <summary>
    /// Перезапуск сервиса (требование модели здоровья: при Unhealthy должен быть endpoint перезапуска).
    /// Процесс корректно останавливается, контейнер поднимает restart policy docker compose.
    /// </summary>
    [HttpPost("restarts")]
    [Authorize(Policy = App.Application.Common.Roles.Admin)]
    [ProducesResponseType(StatusCodes.Status202Accepted)]
    public IActionResult Restart()
    {
        LogRestartRequested(logger, User.Identity?.Name);
        HttpContext.Response.OnCompleted(() =>
        {
            lifetime.StopApplication();
            return Task.CompletedTask;
        });
        return Accepted();
    }

    [LoggerMessage(Level = LogLevel.Warning, Message = "Запрошен перезапуск сервиса пользователем {User}")]
    private static partial void LogRestartRequested(ILogger logger, string? user);
}

public sealed record SystemInfo(string Name, string Version, string Environment, DateTimeOffset ServerTime);
