import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { ButtonModule } from 'primeng/button';
import { MessageModule } from 'primeng/message';
import { ProgressSpinnerModule } from 'primeng/progressspinner';
import { SkeletonModule } from 'primeng/skeleton';

import { Problem } from '../../core/http/problem';
import { ViewStatus } from './loadable';

/**
 * Единая обёртка 5 состояний экрана/блока (rules.md п. 4).
 * Тексты приходят из screen-spec (states.*), problem.detail дополняет текст ошибки.
 */
@Component({
  selector: 'app-state',
  imports: [ButtonModule, MessageModule, ProgressSpinnerModule, SkeletonModule],
  templateUrl: './app-state.component.html',
  styleUrl: './app-state.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AppStateComponent {
  readonly status = input.required<ViewStatus>();
  readonly loadingKind = input<'skeleton' | 'spinner' | 'progress'>('skeleton');
  readonly skeleton = input<'table' | 'cards' | 'detail' | 'map' | 'timeline'>('table');
  readonly emptyTitle = input('Данных нет');
  readonly emptyDescription = input<string | undefined>(undefined);
  readonly emptyActionLabel = input<string | undefined>(undefined);
  readonly errorTitle = input('Не удалось загрузить данные');
  readonly errorDescription = input<string | undefined>(undefined);
  readonly problem = input<Problem | null>(null);
  readonly retryable = input(true);
  readonly forbiddenTitle = input('Нет доступа');
  readonly forbiddenDescription = input<string | undefined>(undefined);
  readonly testid = input('state');
  readonly compact = input(false);

  readonly retry = output<void>();
  readonly emptyAction = output<void>();

  readonly rows = computed(() => (this.compact() ? [1, 2, 3] : [1, 2, 3, 4, 5, 6]));
  readonly errorText = computed(() => {
    const p = this.problem();
    const extra = p?.detail && p.detail !== this.errorTitle() ? p.detail : undefined;
    return [this.errorDescription(), extra].filter(Boolean).join('. ') || undefined;
  });
}
