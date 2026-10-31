namespace App.Domain.Common;

/// <summary>
/// Нарушение бизнес-правила предметной области. API отвечает 422 с <see cref="Code"/> в problem+json.
/// </summary>
public class DomainException : Exception
{
    public DomainException(string code, string message)
        : base(message)
    {
        Code = code;
    }

    public DomainException()
        : this("domain_rule_violated", "Нарушено бизнес-правило.")
    {
    }

    public DomainException(string message)
        : this("domain_rule_violated", message)
    {
    }

    public DomainException(string message, Exception innerException)
        : base(message, innerException)
    {
        Code = "domain_rule_violated";
    }

    /// <summary>Машиночитаемый код ошибки в snake_case.</summary>
    public string Code { get; } = "domain_rule_violated";
}
