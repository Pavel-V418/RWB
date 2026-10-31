// Заглушка backend-агента: модель не справилась за отведённые попытки. Операции отвечают 501.
using App.Application.Common;
using App.Application.Contracts;

namespace App.Application.Features.SyncOperations;

internal sealed class SyncOperationsApi : ISyncOperationsApi
{
    public System.Threading.Tasks.Task<SyncOperationsResponseDto> SyncOperationsAsync(SyncOperationsRequestDto body, System.Threading.CancellationToken cancellationToken) =>
        throw new FeatureNotImplementedException(nameof(SyncOperationsAsync));
}
