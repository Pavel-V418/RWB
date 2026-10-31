using App.Application.Abstractions;
using App.Application.Common;
using App.Application.Contracts;
using App.Domain.Entities;
using Microsoft.EntityFrameworkCore;

namespace App.Application.Features.Users;

/// <summary>
/// Реализация операций тега Users: список пользователей и создание пользователя.
/// </summary>
internal sealed class UsersApi(
    IAppDbContext db,
    ICurrentUser currentUser,
    IPasswordHasher passwordHasher,
    TimeProvider time) : IUsersApi
{
    private static readonly System.Text.RegularExpressions.Regex UsernameRegex =
        new("^[a-zA-Z0-9._-]{3,50}$", System.Text.RegularExpressions.RegexOptions.Compiled);

    public async Task<ListUsersResponseDto> ListUsersAsync(
        ListUsersRole? role,
        bool? isActive,
        int page,
        int pageSize,
        CancellationToken cancellationToken)
    {
        if (page < 1)
        {
            throw new RequestValidationException("page", "Номер страницы должен быть больше или равен 1.");
        }

        if (pageSize < 1 || pageSize > 200)
        {
            throw new RequestValidationException("pageSize", "Размер страницы должен быть от 1 до 200.");
        }

        var query = db.Users.AsNoTracking().AsQueryable();

        if (role is not null)
        {
            var roleValue = ContractEnums.ToValue(role.Value);
            query = query.Where(u => u.Role == roleValue);
        }

        if (isActive is not null)
        {
            query = query.Where(u => u.IsActive == isActive.Value);
        }

        var total = await query.CountAsync(cancellationToken);

        var items = await query
            .OrderBy(u => u.CreatedAt)
            .Skip((page - 1) * pageSize)
            .Take(pageSize)
            .ToListAsync(cancellationToken);

        return new ListUsersResponseDto
        {
            Items = items.Select(ToDto).ToList(),
            Page = page,
            PageSize = pageSize,
            Total = total,
        };
    }

    public async Task<UserDto> CreateUserAsync(CreateUserRequestDto body, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(body);

        if (string.IsNullOrWhiteSpace(body.Username) || body.Username.Length > 50 || !UsernameRegex.IsMatch(body.Username))
        {
            throw new RequestValidationException("username", "Имя пользователя должно содержать от 3 до 50 символов: латинские буквы, цифры, точка, подчёркивание, дефис.");
        }

        var existing = await db.Users.AsNoTracking()
            .AnyAsync(u => u.Username == body.Username, cancellationToken);

        if (existing)
        {
            throw new ConflictException("username_exists", "Пользователь с таким именем уже существует.");
        }

        if (body.EmployeeId is not null)
        {
            var employeeExists = await db.Employees.AsNoTracking()
                .AnyAsync(e => e.Id == body.EmployeeId.Value, cancellationToken);

            if (!employeeExists)
            {
                throw new RequestValidationException("employeeId", "Сотрудник с указанным идентификатором не найден.");
            }
        }

        var now = time.GetUtcNow();
        var roleValue = ContractEnums.ToValue(body.Role);

        var user = new User
        {
            Id = Guid.NewGuid(),
            Username = body.Username,
            PasswordHash = passwordHasher.Hash(string.Empty),
            Role = roleValue,
            EmployeeId = body.EmployeeId,
            IsActive = body.IsActive,
            CreatedAt = now,
            UpdatedAt = now,
        };

        var auditLog = new AuditLog
        {
            Id = Guid.NewGuid(),
            UserId = user.Id,
            Action = "user_created",
            EntityType = "User",
            EntityId = user.Id,
            Timestamp = now,
            Details = $"User '{body.Username}' created with role '{roleValue}'.",
            CreatedAt = now,
            UpdatedAt = now,
        };

        db.Users.Add(user);
        db.AuditLogs.Add(auditLog);

        await db.SaveChangesAsync(cancellationToken);

        return ToDto(user);
    }

    private static UserDto ToDto(User user) => new()
    {
        Id = user.Id,
        Username = user.Username,
        Role = ContractEnums.Parse<UserRole>(user.Role),
        EmployeeId = user.EmployeeId,
        IsActive = user.IsActive,
        CreatedAt = user.CreatedAt,
        UpdatedAt = user.UpdatedAt,
    };
}
