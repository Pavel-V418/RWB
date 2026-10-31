using App.Application.Abstractions;
using App.Application.Common;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.SignalR;

namespace App.Api.Realtime;

/// <summary>
/// SignalR-хаб /hubs/events. Администраторы автоматически получают все события,
/// остальные — только по ключам, на которые подписались (если разрешает <see cref="ISubscriptionGuard"/>).
/// Имя клиентского метода = тип события, аргумент = <c>EventEnvelope</c>.
/// </summary>
[Authorize]
public sealed class EventsHub(ICurrentUser currentUser, ISubscriptionGuard guard) : Hub
{
    public const string Path = "/hubs/events";
    public const string AdminsGroup = "admins";

    public static string KeyGroup(string subscriptionKey) => "key:" + subscriptionKey;

    public override async Task OnConnectedAsync()
    {
        if (currentUser.IsInRole(Roles.Admin))
        {
            await Groups.AddToGroupAsync(Context.ConnectionId, AdminsGroup);
        }

        await base.OnConnectedAsync();
    }

    /// <summary>Подписаться на события по ключу (например, id автомобиля).</summary>
    public async Task Subscribe(string subscriptionKey)
    {
        if (!await guard.CanSubscribeAsync(currentUser, subscriptionKey, Context.ConnectionAborted))
        {
            throw new HubException("Подписка запрещена.");
        }

        await Groups.AddToGroupAsync(Context.ConnectionId, KeyGroup(subscriptionKey));
    }

    public Task Unsubscribe(string subscriptionKey) =>
        Groups.RemoveFromGroupAsync(Context.ConnectionId, KeyGroup(subscriptionKey));
}

internal sealed class SignalRRealtimeNotifier(IHubContext<EventsHub> hub) : IRealtimeNotifier
{
    public Task PublishAsync(string method, object payload, string? subscriptionKey, CancellationToken cancellationToken)
    {
        var clients = subscriptionKey is null
            ? hub.Clients.Group(EventsHub.AdminsGroup)
            : hub.Clients.Groups(EventsHub.AdminsGroup, EventsHub.KeyGroup(subscriptionKey));
        return clients.SendAsync(method, payload, cancellationToken);
    }
}
