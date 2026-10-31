using App.Application.Abstractions;
using App.Application.Common;
using App.Application.Contracts;
using App.Domain.Entities;
using Microsoft.EntityFrameworkCore;

namespace App.Application.Features.Employees;

/// <summary>
/// Реализация операций тега Employees: список сотрудников с фильтрами и пагинацией,
/// поиск сотрудника по номеру пропуска.
/// </summary>
internal sealed class EmployeesApi(
    IAppDbContext db,
    ICurrentUser currentUser) : IEmployeesApi
{
    public async Task<ListEmployeesResponseDto> ListEmployeesAsync(
        string? name,
        string? passNumber,
        int page,
        int pageSize,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(name is null ? null : name);
        ArgumentNullException.ThrowIfNull(passNumber is null ? null : passNumber);

        if (page < 1)
        {
            throw new RequestValidationException("page", "Номер страницы должен быть больше или равен 1.");
        }

        if (pageSize < 1 || pageSize > 200)
        {
            throw new RequestValidationException("pageSize", "Размер страницы должен быть от 1 до 200.");
        }

        var query = db.Employees.AsNoTracking().AsQueryable();

        if (!string.IsNullOrWhiteSpace(name))
        {
            query = query.Where(e => e.Name.Contains(name, StringComparison.OrdinalIgnoreCase));
        }

        if (!string.IsNullOrWhiteSpace(passNumber))
        {
            query = query.Where(e => e.PassNumber == passNumber);
        }

        var total = await query.CountAsync(cancellationToken);

        var items = await query
            .OrderBy(e => e.Name)
            .Skip((page - 1) * pageSize)
            .Take(pageSize)
            .ToListAsync(cancellationToken);

        return new ListEmployeesResponseDto
        {
            Items = items.Select(ToDto).ToList(),
            Page = page,
            PageSize = pageSize,
            Total = total,
        };
    }

    public async Task<EmployeeDto> GetEmployeeByPassNumberAsync(
        string passNumber,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(passNumber);

        if (string.IsNullOrWhiteSpace(passNumber))
        {
            throw new RequestValidationException("passNumber", "Номер пропуска не может быть пустым.");
        }

        var employee = await db.Employees.AsNoTracking()
            .SingleOrDefaultAsync(e => e.PassNumber == passNumber, cancellationToken)
            ?? throw new NotFoundException("Сотрудник", passNumber);

        return ToDto(employee);
    }

    private static EmployeeDto ToDto(Employee employee) => new()
    {
        Id = employee.Id,
        Name = employee.Name,
        PassNumber = employee.PassNumber,
        CreatedAt = employee.CreatedAt,
        UpdatedAt = employee.UpdatedAt,
    };
}
