using App.Domain.Common;
using App.Infrastructure.Messaging;
using App.Infrastructure.Security;
using Microsoft.Extensions.Options;
using Microsoft.IdentityModel.JsonWebTokens;
using Microsoft.IdentityModel.Tokens;

namespace App.UnitTests;

public sealed class PasswordHasherTests
{
    private readonly Pbkdf2PasswordHasher _hasher = new();

    [Fact]
    public void Verify_ReturnsTrue_ForSamePassword()
    {
        var hash = _hasher.Hash("correct horse battery");

        Assert.True(_hasher.Verify("correct horse battery", hash));
    }

    [Fact]
    public void Verify_ReturnsFalse_ForWrongPasswordOrBrokenHash()
    {
        var hash = _hasher.Hash("correct horse battery");

        Assert.False(_hasher.Verify("wrong", hash));
        Assert.False(_hasher.Verify("correct horse battery", "not-a-hash"));
    }

    [Fact]
    public void Hash_UsesRandomSalt()
    {
        Assert.NotEqual(_hasher.Hash("same"), _hasher.Hash("same"));
    }
}

public sealed class JwtTokenServiceTests
{
    private static readonly JwtOptions Options = new()
    {
        Issuer = "test-issuer",
        Audience = "test-audience",
        SigningKey = "unit-test-signing-key-0123456789abcdef",
        LifetimeMinutes = 10,
    };

    [Fact]
    public async Task Issue_ProducesTokenValidWithSameKey()
    {
        var userId = Guid.NewGuid();
        var service = new JwtTokenService(Microsoft.Extensions.Options.Options.Create(Options), TimeProvider.System);

        var token = service.Issue(userId, "driver1", "driver");
        var result = await new JsonWebTokenHandler().ValidateTokenAsync(token.Token, new TokenValidationParameters
        {
            ValidIssuer = Options.Issuer,
            ValidAudience = Options.Audience,
            IssuerSigningKey = Options.CreateKey(),
        });

        Assert.True(result.IsValid, result.Exception?.Message);
        Assert.Equal(userId.ToString(), result.Claims[JwtRegisteredClaimNames.Sub]);
        Assert.Equal("driver", result.Claims["role"]);
        Assert.Equal(600, token.ExpiresInSeconds);
    }

    [Fact]
    public void CreateKey_RejectsShortKey()
    {
        var options = new JwtOptions { SigningKey = "short" };

        Assert.Throws<InvalidOperationException>(() => options.CreateKey());
    }
}

public sealed class EventEnvelopeTests
{
    private sealed record SampleHappened(Guid ItemId, int Amount, DateTimeOffset OccurredAt) : IIntegrationEvent
    {
        public string EventType => "SampleHappened";

        public int Version => 2;

        public string Topic => "samples";

        public string PartitionKey => ItemId.ToString();
    }

    [Fact]
    public void From_MovesServiceFieldsToEnvelope_AndKeepsDomainFieldsInPayload()
    {
        var itemId = Guid.NewGuid();
        var occurredAt = new DateTimeOffset(2026, 10, 30, 12, 0, 0, TimeSpan.FromHours(3));
        var eventId = Guid.NewGuid();

        var envelope = EventEnvelope.From(new SampleHappened(itemId, 42, occurredAt), eventId);

        Assert.Equal(eventId, envelope.EventId);
        Assert.Equal("SampleHappened", envelope.Type);
        Assert.Equal(2, envelope.Version);
        Assert.Equal(TimeSpan.Zero, envelope.OccurredAt.Offset);
        Assert.Equal(itemId.ToString(), envelope.PartitionKey);
        Assert.Equal(42, (int)envelope.Payload["amount"]!);
        Assert.Equal(itemId.ToString(), (string)envelope.Payload["itemId"]!);
        Assert.False(envelope.Payload.ContainsKey("topic"));
        Assert.False(envelope.Payload.ContainsKey("eventType"));
        Assert.False(envelope.Payload.ContainsKey("occurredAt"));
    }

    [Fact]
    public void Json_RoundTrip_PreservesEnvelope()
    {
        var envelope = EventEnvelope.From(new SampleHappened(Guid.NewGuid(), 7, DateTimeOffset.UtcNow), Guid.NewGuid());

        var restored = EventEnvelope.FromJson(envelope.ToJson());

        Assert.NotNull(restored);
        Assert.Equal(envelope.EventId, restored.EventId);
        Assert.Equal(envelope.Type, restored.Type);
        Assert.Equal(7, (int)restored.Payload["amount"]!);
    }
}

public sealed class GeoConstantsTests
{
    [Fact]
    public void CreatePoint_StoresLonAsX_LatAsY_WithWgs84Srid()
    {
        var point = GeoConstants.CreatePoint(lat: 59.9554, lon: 30.3068);

        Assert.Equal(30.3068, point.X);
        Assert.Equal(59.9554, point.Y);
        Assert.Equal(4326, point.SRID);
    }
}
