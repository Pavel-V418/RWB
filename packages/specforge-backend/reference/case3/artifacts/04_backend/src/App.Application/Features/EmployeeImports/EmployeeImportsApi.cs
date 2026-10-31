using App.Application.Abstractions;
using App.Application.Common;
using App.Application.Contracts;
using App.Domain.Entities;
using Microsoft.EntityFrameworkCore;
using System.Text.Json;

namespace App.Application.Features.EmployeeImports;

/// <summary>
/// Реализация операций импорта сотрудников из CSV.
/// </summary>
internal sealed class EmployeeImportsApi(
    IAppDbContext db,
    ICurrentUser currentUser,
    TimeProvider time) : IEmployeeImportsApi
{
    private const int MaxFileLength = 255;
    private const int MaxNameLength = 200;
    private const int MaxPassNumberLength = 32;

    private static readonly string[] CsvLineSeparators = ["\r\n", "\n"];

    public async Task<ImportEmployeesResponseDto> ImportEmployeesAsync(
        ImportEmployeesRequestDto body,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(body);

        // 1. Проверка прав роли system_admin
        if (!currentUser.IsInRole(Roles.SystemAdmin))
        {
            throw new ForbiddenException();
        }

        // 2. Валидация формата CSV-файла
        var fileContent = body.File;
        if (string.IsNullOrWhiteSpace(fileContent))
        {
            throw new RequestValidationException("File", "Файл не может быть пустым.");
        }

        if (fileContent.Length > MaxFileLength)
        {
            throw new RequestValidationException("File", $"Длина файла не должна превышать {MaxFileLength} символов.");
        }

        var lines = fileContent.Split(CsvLineSeparators, StringSplitOptions.RemoveEmptyEntries);
        if (lines.Length == 0)
        {
            throw new RequestValidationException("File", "Файл не содержит данных.");
        }

        // Проверка заголовка
        var header = lines[0].Split(',').Select(h => h.Trim()).ToArray();
        if (header.Length < 2 || !header[0].Equals("name", StringComparison.OrdinalIgnoreCase) ||
            !header[1].Equals("passNumber", StringComparison.OrdinalIgnoreCase))
        {
            throw new RequestValidationException("File", "Некорректный формат CSV. Ожидаются заголовки: name,passNumber");
        }

        // 3. Парсинг и валидация строк
        var employeesToImport = new List<(string Name, string PassNumber)>();
        var errors = new List<string>();
        var seenPassNumbers = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

        for (int i = 1; i < lines.Length; i++)
        {
            var line = lines[i];
            var parts = line.Split(',').Select(p => p.Trim()).ToArray();

            if (parts.Length < 2)
            {
                errors.Add($"Строка {i + 1}: некорректное количество полей.");
                continue;
            }

            var name = parts[0];
            var passNumber = parts[1];

            if (string.IsNullOrWhiteSpace(name))
            {
                errors.Add($"Строка {i + 1}: имя не может быть пустым.");
                continue;
            }

            if (name.Length > MaxNameLength)
            {
                errors.Add($"Строка {i + 1}: имя превышает {MaxNameLength} символов.");
                continue;
            }

            if (string.IsNullOrWhiteSpace(passNumber))
            {
                errors.Add($"Строка {i + 1}: номер пропуска не может быть пустым.");
                continue;
            }

            if (passNumber.Length > MaxPassNumberLength)
            {
                errors.Add($"Строка {i + 1}: номер пропуска превышает {MaxPassNumberLength} символов.");
                continue;
            }

            // Проверка уникальности passNumber внутри файла
            if (!seenPassNumbers.Add(passNumber))
            {
                errors.Add($"Строка {i + 1}: дубликат номера пропуска '{passNumber}'.");
                continue;
            }

            employeesToImport.Add((name, passNumber));
        }

        // Если есть ошибки валидации — возвращаем 422
        if (errors.Count > 0)
        {
            throw new BusinessRuleException("invalid_csv_format", string.Join("; ", errors));
        }

        // 4. Проверка уникальности passNumber в БД
        var passNumbers = employeesToImport.Select(e => e.PassNumber).ToList();
        var existingEmployees = await db.Employees.AsNoTracking()
            .Where(e => passNumbers.Contains(e.PassNumber))
            .Select(e => e.PassNumber)
            .ToListAsync(cancellationToken);

        if (existingEmployees.Count > 0)
        {
            throw new BusinessRuleException("duplicate_pass_number",
                $"Дубликаты номеров пропуска в базе данных: {string.Join(", ", existingEmployees)}");
        }

        // 5. Импортировать данные в таблицу Employee
        var now = time.GetUtcNow();
        var importedCount = 0;

        foreach (var (name, passNumber) in employeesToImport)
        {
            var employee = new Employee
            {
                Id = Guid.NewGuid(),
                Name = name,
                PassNumber = passNumber,
                CreatedAt = now,
                UpdatedAt = now
            };

            db.Employees.Add(employee);
            importedCount++;
        }

        // 6. Зафиксировать действие в AuditLog
        var auditLog = new AuditLog
        {
            Id = Guid.NewGuid(),
            UserId = currentUser.UserId,
            Action = "ImportEmployees",
            EntityType = "Employee",
            EntityId = null,
            Timestamp = now,
            Details = JsonSerializer.Serialize(new
            {
                ImportedCount = importedCount,
                SkippedCount = 0,
                ErrorCount = 0
            }),
            CreatedAt = now,
            UpdatedAt = now
        };

        db.AuditLogs.Add(auditLog);

        await db.SaveChangesAsync(cancellationToken);

        // 7. Вернуть результат импорта
        return new ImportEmployeesResponseDto();
    }
}
