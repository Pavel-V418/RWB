// Слот реализован вручную (консоль конвейера). Эффективность прогона: токены, время, попытки.
// Требования: FR-05
import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { CardModule } from 'primeng/card';
import { MessageModule } from 'primeng/message';
import { ProgressBarModule } from 'primeng/progressbar';
import { TagModule } from 'primeng/tag';

import { Run, RunMetrics } from '../generated/api/models';
import { fmtDuration, fmtNumber } from '../shared/value/format';

@Component({
  selector: 'slot-run-detail-metrics',
  imports: [CardModule, MessageModule, ProgressBarModule, TagModule],
  templateUrl: './run-detail-metrics.slot.html',
  styleUrl: './run-detail-metrics.slot.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class RunDetailMetricsSlot {
  readonly data = input<Record<string, unknown>>({});

  protected readonly metrics = computed(() => this.data()['metrics'] as RunMetrics | null);
  protected readonly simulated = computed(() => !!(this.data()['primary'] as Run | null)?.simulated);
  private readonly titles = computed(() => new Map(((this.data()['primary'] as Run | null)?.stages ?? []).map((s) => [s.id, s.title])));
  protected readonly rows = computed(() => {
    const st = this.metrics()?.stages ?? [];
    const max = Math.max(1, ...st.map((s) => (s.tokensIn ?? 0) + (s.tokensOut ?? 0)));
    return st.map((s) => ({
      id: s.stageId ?? '',
      title: this.titles().get(s.stageId ?? '') ?? s.stageId ?? '',
      tokens: (s.tokensIn ?? 0) + (s.tokensOut ?? 0),
      share: Math.round((((s.tokensIn ?? 0) + (s.tokensOut ?? 0)) / max) * 100),
      cached: s.tokensCached ?? 0,
      attempts: s.attempts ?? 0,
      calls: s.llmCalls ?? 0,
      seconds: s.durationSec ?? 0,
    }));
  });
  protected readonly tiles = computed(() => {
    const t = this.metrics()?.totals;
    if (!t) return [];
    const tin = t.tokensIn ?? 0;
    return [
      { label: 'Токены (вход / выход)', value: `${fmtNumber(tin)} / ${fmtNumber(t.tokensOut ?? 0)}` },
      { label: 'Из кэша префиксов', value: tin ? `${Math.round(((t.tokensCached ?? 0) / tin) * 100)}%` : '—' },
      { label: 'Вызовов модели', value: fmtNumber(t.llmCalls ?? 0) },
      { label: 'Попыток (с исправлениями)', value: fmtNumber(t.attempts ?? 0) },
      { label: 'Длительность', value: fmtDuration(t.durationSec ?? 0) || '—' },
      { label: 'Код без LLM', value: t.deterministicShare != null ? `${Math.round(t.deterministicShare * 100)}%` : '—' },
    ];
  });

  protected fmt(n: number): string {
    return fmtNumber(n);
  }

  protected dur(s: number): string {
    return fmtDuration(s) || '—';
  }
}
