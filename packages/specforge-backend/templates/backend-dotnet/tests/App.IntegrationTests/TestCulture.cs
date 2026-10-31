using System.Globalization;
using System.Runtime.CompilerServices;

namespace App.IntegrationTests;

/// <summary>
/// Тесты собирают URL интерполяцией (<c>$"?lat={lat}"</c>): в культуре ru-RU число уходит как «59,93» и API
/// отвечает 400. Фиксируем инвариантную культуру на весь процесс тестов — результат не зависит от машины.
/// </summary>
internal static class TestCulture
{
#pragma warning disable CA2255 // тестовый процесс — не библиотека, инициализатор нужен до первого теста
    [ModuleInitializer]
    internal static void Initialize()
#pragma warning restore CA2255
    {
        CultureInfo.DefaultThreadCurrentCulture = CultureInfo.InvariantCulture;
        CultureInfo.DefaultThreadCurrentUICulture = CultureInfo.InvariantCulture;
        CultureInfo.CurrentCulture = CultureInfo.InvariantCulture;
        CultureInfo.CurrentUICulture = CultureInfo.InvariantCulture;
    }
}
