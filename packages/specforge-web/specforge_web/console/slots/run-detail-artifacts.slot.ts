// Слот реализован вручную (консоль конвейера). Артефакты прогона по этапам и просмотр содержимого.
// Требования: FR-04
import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, input, signal } from '@angular/core';
import { ButtonModule } from 'primeng/button';
import { CardModule } from 'primeng/card';
import { DialogModule } from 'primeng/dialog';
import { MessageModule } from 'primeng/message';
import { ProgressSpinnerModule } from 'primeng/progressspinner';
import { TagModule } from 'primeng/tag';

import { ApiService } from '../generated/api/api.service';
import { Artifact, Run } from '../generated/api/models';
import { fmtNumber } from '../shared/value/format';

type View = { kind: 'text'; text: string; truncated: boolean } | { kind: 'image'; url: string } | { kind: 'error'; message: string };

const IMAGE = /\.(png|jpe?g|gif|webp|svg)$/i;
const MAX_TEXT = 200_000;
const KIND_LABEL: Record<string, string> = {
  markdown: 'Markdown', json: 'JSON', yaml: 'YAML', code: 'Код', image: 'Изображение', report: 'Отчёт', other: 'Файл',
};

/** Красивый вывод JSON; остальное — как есть. */
export function prettyText(path: string, text: string): string {
  if (/\.json$/i.test(path)) {
    try {
      return JSON.stringify(JSON.parse(text), null, 2);
    } catch {
      return text;
    }
  }
  return text;
}

@Component({
  selector: 'slot-run-detail-artifacts',
  imports: [ButtonModule, CardModule, DialogModule, MessageModule, ProgressSpinnerModule, TagModule],
  templateUrl: './run-detail-artifacts.slot.html',
  styleUrl: './run-detail-artifacts.slot.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class RunDetailArtifactsSlot {
  readonly data = input<Record<string, unknown>>({});
  private readonly api = inject(ApiService);

  protected readonly run = computed(() => this.data()['primary'] as Run | null);
  private readonly list = computed(() => {
    const raw = this.data()['artifacts'];
    return (Array.isArray(raw) ? raw : []) as Artifact[];
  });
  /** Группы по этапам в порядке этапов прогона; файлы без этапа — в конце. */
  protected readonly groups = computed(() => {
    const stages = this.run()?.stages ?? [];
    const byStage = new Map<string, Artifact[]>();
    for (const a of this.list()) {
      const key = a.stageId ?? a.path.split('/')[0] ?? '';
      (byStage.get(key) ?? byStage.set(key, []).get(key)!).push(a);
    }
    const order = [...stages.map((s) => s.id), ...[...byStage.keys()].filter((k) => !stages.some((s) => s.id === k))];
    return order
      .filter((id) => byStage.has(id))
      .map((id) => ({ id, title: stages.find((s) => s.id === id)?.title ?? id, files: byStage.get(id)! }));
  });

  protected readonly open = signal(false);
  protected readonly selected = signal<Artifact | null>(null);
  protected readonly loading = signal(false);
  protected readonly view = signal<View | null>(null);
  private objectUrl: string | null = null;
  private blob: Blob | null = null;

  constructor() {
    inject(DestroyRef).onDestroy(() => this.release());
  }

  protected name(a: Artifact): string {
    return a.path.split('/').slice(1).join('/') || a.path;
  }

  protected kindLabel(a: Artifact): string {
    return KIND_LABEL[a.kind ?? (IMAGE.test(a.path) ? 'image' : 'other')] ?? 'Файл';
  }

  protected size(a: Artifact): string {
    return a.size >= 1024 ? `${fmtNumber(Math.round(a.size / 102.4) / 10)} КБ` : `${fmtNumber(a.size)} Б`;
  }

  protected show(a: Artifact): void {
    const run = this.run();
    if (!run) return;
    this.release();
    this.selected.set(a);
    this.view.set(null);
    this.loading.set(true);
    this.open.set(true);
    this.api.getRunArtifactContent({ runId: run.id, path: a.path }, { silent: true }).subscribe({
      next: (blob) => {
        this.blob = blob;
        if (IMAGE.test(a.path) || a.kind === 'image' || blob.type.startsWith('image/')) {
          this.objectUrl = URL.createObjectURL(blob);
          this.view.set({ kind: 'image', url: this.objectUrl });
          this.loading.set(false);
          return;
        }
        blob.text().then(
          (t) => {
            const cut = t.length > MAX_TEXT;
            this.view.set({ kind: 'text', text: prettyText(a.path, cut ? t.slice(0, MAX_TEXT) : t), truncated: cut });
            this.loading.set(false);
          },
          () => this.fail('Не удалось прочитать файл'),
        );
      },
      error: (e: unknown) =>
        this.fail((e as { status?: number } | null)?.status === 404
          ? 'Файл ещё не создан или недоступен'
          : 'Не удалось загрузить файл — проверьте, что оркестратор запущен'),
    });
  }

  protected download(): void {
    const a = this.selected();
    if (!a || !this.blob) return;
    const url = this.objectUrl ?? URL.createObjectURL(this.blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = a.path.split('/').pop() ?? 'artifact';
    link.click();
    if (!this.objectUrl) setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  protected close(): void {
    this.open.set(false);
    this.release();
  }

  private fail(message: string): void {
    this.view.set({ kind: 'error', message });
    this.loading.set(false);
  }

  private release(): void {
    if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
    this.objectUrl = null;
    this.blob = null;
  }
}
