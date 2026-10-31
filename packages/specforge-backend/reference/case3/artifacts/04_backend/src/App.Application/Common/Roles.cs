namespace App.Application.Common;

/// <summary>
/// Роли из JWT (claim "role"). Здесь — только роль платформы (системные эндпоинты, хаб событий);
/// роли предметной области генерирует gen_aspnet.py из x-roles контракта: Contracts/Generated/Roles.cs.
/// </summary>
public static partial class Roles
{
    public const string Admin = "admin";
}
