using System.Collections.Concurrent;
using System.Reflection;
using System.Text.Json.Serialization;

namespace App.Application.Common;

/// <summary>
/// Перевод между enum контракта (DTO) и строковым значением из БД/контракта.
/// Значение берётся из <see cref="JsonStringEnumMemberNameAttribute"/>: SessionStatus.Active ↔ "active".
/// </summary>
public static class ContractEnums
{
    private static readonly ConcurrentDictionary<Type, (Dictionary<string, object> ByValue, Dictionary<object, string> ByMember)> Cache = new();

    /// <summary>"active" → SessionStatus.Active. Неизвестное значение — <see cref="InvalidOperationException"/>.</summary>
    public static TEnum Parse<TEnum>(string value)
        where TEnum : struct, Enum =>
        Map(typeof(TEnum)).ByValue.TryGetValue(value, out var member)
            ? (TEnum)member
            : throw new InvalidOperationException($"Значение '{value}' не входит в {typeof(TEnum).Name}.");

    /// <summary>SessionStatus.Active → "active".</summary>
    public static string ToValue<TEnum>(TEnum member)
        where TEnum : struct, Enum => Map(typeof(TEnum)).ByMember[member];

    private static (Dictionary<string, object> ByValue, Dictionary<object, string> ByMember) Map(Type type) =>
        Cache.GetOrAdd(type, t =>
        {
            var byValue = new Dictionary<string, object>(StringComparer.Ordinal);
            var byMember = new Dictionary<object, string>();
            foreach (var field in t.GetFields(BindingFlags.Public | BindingFlags.Static))
            {
                var value = field.GetCustomAttribute<JsonStringEnumMemberNameAttribute>()?.Name ?? field.Name;
                var member = field.GetValue(null)!;
                byValue[value] = member;
                byMember[member] = value;
            }

            return (byValue, byMember);
        });
}
