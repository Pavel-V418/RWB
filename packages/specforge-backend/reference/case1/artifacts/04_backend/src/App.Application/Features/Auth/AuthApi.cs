using App.Application.Abstractions;
using App.Application.Common;
using App.Application.Contracts;
using App.Domain.Entities;
using Microsoft.EntityFrameworkCore;

namespace App.Application.Features.Auth;

/// <summary>
/// Эталон реализации I&lt;Tag&gt;Api: данные через <see cref="IAppDbContext"/>, ошибки — исключения
/// App.Application.Common (обработчик API превращает их в RFC 9457), маппинг сущность → DTO — в этом же файле.
/// </summary>
internal sealed class AuthApi(
    IAppDbContext db,
    IPasswordHasher passwordHasher,
    ITokenService tokens,
    ICurrentUser currentUser) : IAuthApi
{
    public async Task<LoginResponseDto> LoginAsync(LoginRequestDto body, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(body);
        var user = await db.Users.AsNoTracking()
            .SingleOrDefaultAsync(u => u.Login == body.Login && u.IsActive, cancellationToken);

        // Одинаковый ответ для «нет пользователя» и «неверный пароль» — не раскрываем существование логина
        if (user is null || !passwordHasher.Verify(body.Password, user.PasswordHash))
        {
            throw new UnauthorizedException();
        }

        var token = tokens.Issue(user.Id, user.Login, user.Role);
        return new LoginResponseDto
        {
            AccessToken = token.Token,
            ExpiresIn = token.ExpiresInSeconds,
            User = ToDto(user),
        };
    }

    public async Task<UserDto> GetCurrentUserAsync(CancellationToken cancellationToken)
    {
        var userId = currentUser.UserId ?? throw new UnauthorizedException("Требуется аутентификация.");
        var user = await db.Users.AsNoTracking().SingleOrDefaultAsync(u => u.Id == userId, cancellationToken)
            ?? throw new NotFoundException("Пользователь", userId);
        return ToDto(user);
    }

    private static UserDto ToDto(User user) => new()
    {
        Id = user.Id,
        Login = user.Login,
        Role = user.Role == Roles.Admin ? UserRole.Admin : UserRole.Driver,
        FullName = user.FullName,
        Phone = user.Phone,
    };
}
