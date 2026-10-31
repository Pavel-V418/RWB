using App.Application.Integrations;
using App.Infrastructure.External;
using App.Infrastructure.Integrations;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;

namespace App.Infrastructure;

public static partial class DependencyInjection
{
    static partial void AddInfrastructureServices(IServiceCollection services, IConfiguration configuration)
    {
        // Providers:Parkomatika:BaseUrl (на стенде — http://parkomatika-mock:8090/)
        services.AddExternalProvider<IParkingPaymentProvider, ParkomatikaPaymentProvider>(configuration, "Parkomatika");
    }
}
