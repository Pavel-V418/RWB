// Слот реализован вручную (консоль конвейера). Живой граф этапов прогона.
// Требования: FR-02, FR-03
import { ChangeDetectionStrategy, Component, DestroyRef, computed, effect, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MessageService } from 'primeng/api';
import { ButtonModule } from 'primeng/button';
import { CardModule } from 'primeng/card';
import { MessageModule } from 'primeng/message';
import { ProgressBarModule } from 'primeng/progressbar';
import { TagModule } from 'primeng/tag';
import { TextareaModule } from 'primeng/textarea';

import { environment } from '../../environments/environment';
import { ApiService } from '../generated/api/api.service';
import { Run, RunEvent, Stage } from '../generated/api/models';

type Sev = 'success' | 'info' | 'warn' | 'danger' | 'secondary' | 'contrast';
const STATUS: Record<string, { label: string; severity: Sev }> = {
  pending: { label: 'Ожидает', severity: 'secondary' },
  running: { label: 'Выполняется', severity: 'info' },
  gate: { label: 'Проверка шлюза', severity: 'info' },
  repairing: { label: 'Исправление', severity: 'warn' },
  awaiting_approval: { label: 'Ждёт решения', severity: 'warn' },
  passed: { label: 'Шлюз пройден', severity: 'success' },
  failed: { label: 'Ошибка', severity: 'danger' },
  skipped: { label: 'Пропущен', severity: 'secondary' },
};
const ACTIVE = new Set(['running', 'gate', 'repairing']);
const MAX_FILES = 12;

/** Блокирующие вопросы этапа без ответа (ни в форме, ни уже сохранённого). */
export function unansweredBlocking(stage: Stage, answers: Record<string, string>): string[] {
  return (stage.questions ?? []).filter((q) => q.blocking && !(answers[q.id] ?? q.answer ?? '').trim()).map((q) => q.id);
}

/** Уровни графа: этап стоит правее всех, от кого зависит. */
export function stageLevels(stages: Stage[]): Stage[][] {
  const byId = new Map(stages.map((s) => [s.id, s]));
  const depth = new Map<string, number>();
  const visit = (s: Stage, guard = 0): number => {
    if (depth.has(s.id)) return depth.get(s.id)!;
    const deps = (s.dependsOn ?? []).map((d) => byId.get(d)).filter((x): x is Stage => !!x);
    const d = guard > 50 || !deps.length ? 0 : 1 + Math.max(...deps.map((x) => visit(x, guard + 1)));
    depth.set(s.id, d);
    return d;
  };
  stages.forEach((s) => visit(s));
  const cols: Stage[][] = [];
  stages.forEach((s) => (cols[depth.get(s.id)!] ??= []).push(s));
  return cols.filter(Boolean);
}

@Component({
  selector: 'slot-run-detail-pipeline-graph',
  imports: [FormsModule, ButtonModule, CardModule, MessageModule, ProgressBarModule, TagModule, TextareaModule],
  templateUrl: './run-detail-pipeline-graph.slot.html',
  styleUrl: './run-detail-pipeline-graph.slot.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class RunDetailPipelineGraphSlot {
  readonly data = input<Record<string, unknown>>({});
  private readonly api = inject(ApiService);
  private readonly messages = inject(MessageService);

  protected readonly run = computed(() => this.data()['primary'] as Run | null);
  private readonly live = signal<Stage[] | null>(null);
  protected readonly stages = computed(() => this.live() ?? this.run()?.stages ?? []);
  protected readonly columns = computed(() => stageLevels(this.stages()));
  protected readonly connected = signal(false);
  protected readonly selected = signal<string | null>(null);
  protected readonly busy = signal<string | null>(null);
  protected readonly comment = signal('');
  protected readonly answers = signal<Record<string, string>>({});
  protected readonly rejectTried = signal(false);
  protected readonly appUrl = computed(() => this.run()?.appUrl || null);
  protected readonly selectedStage = computed(() => {
    const st = this.stages();
    return st.find((s) => s.id === this.selected()) ?? st.find((s) => s.status === 'awaiting_approval' || s.status === 'failed') ?? null;
  });
  protected readonly progress = computed(() => {
    const st = this.stages();
    return st.length ? Math.round((st.filter((s) => s.status === 'passed' || s.status === 'skipped').length / st.length) * 100) : 0;
  });
  private source: EventSource | null = null;
  private sourceRunId: string | null = null;

  constructor() {
    // свежие данные опроса заменяют накопленные SSE-обновления
    effect(() => {
      this.run();
      this.live.set(null);
    });
    effect(() => {
      const id = this.run()?.id;
      if (id && id !== this.sourceRunId) this.connect(id);
    });
    inject(DestroyRef).onDestroy(() => this.source?.close());
  }

  private connect(runId: string): void {
    this.source?.close();
    this.sourceRunId = runId;
    if (typeof EventSource === 'undefined') return;
    const es = new EventSource(`${environment.apiBaseUrl}/runs/${encodeURIComponent(runId)}/events`);
    this.source = es;
    es.onopen = () => this.connected.set(true);
    es.onerror = () => this.connected.set(false); // браузер переподключится сам с Last-Event-ID; до тех пор работает опрос
    es.onmessage = (m) => this.onEvent(m.data);
    for (const t of ['stage.started', 'stage.gate', 'stage.repair', 'stage.passed', 'stage.failed', 'stage.skipped', 'run.awaiting_approval', 'run.finished']) {
      es.addEventListener(t, (m) => this.onEvent((m as MessageEvent).data));
    }
  }

  private onEvent(raw: string): void {
    let e: RunEvent;
    try {
      e = JSON.parse(raw) as RunEvent;
    } catch {
      return;
    }
    if (e.stage) {
      const upd = e.stage;
      this.live.set(this.stages().map((s) => (s.id === upd.id ? upd : s)));
    }
    if (e.type === 'run.finished') this.source?.close();
  }

  protected label(status: string): string {
    return STATUS[status]?.label ?? status;
  }

  protected sev(status: string): Sev {
    return STATUS[status]?.severity ?? 'secondary';
  }

  protected active(status: string): boolean {
    return ACTIVE.has(status);
  }

  protected tokens(s: Stage): string {
    const n = (s.tokensIn ?? 0) + (s.tokensOut ?? 0);
    return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
  }

  protected select(id: string): void {
    this.selected.set(this.selected() === id ? null : id);
  }

  protected answer(id: string): string {
    return this.answers()[id] ?? '';
  }

  protected setAnswer(id: string, value: string): void {
    this.answers.set({ ...this.answers(), [id]: value });
  }

  protected unanswered(stage: Stage): string[] {
    return unansweredBlocking(stage, this.answers());
  }

  protected shownFiles(stage: Stage): string[] {
    return (stage.artifacts ?? []).slice(0, MAX_FILES);
  }

  protected openApp(url: string): void {
    window.open(url, '_blank', 'noopener');
  }

  protected decide(stage: Stage, decision: 'approve' | 'reject'): void {
    const run = this.run();
    if (!run) return;
    const comment = this.comment().trim();
    if (decision === 'reject' && !comment) {
      this.rejectTried.set(true); // без комментария агенту нечего исправлять
      return;
    }
    if (decision === 'approve' && this.unanswered(stage).length) return;
    const answers = Object.fromEntries(Object.entries(this.answers()).map(([k, v]) => [k, v.trim()]).filter(([, v]) => v));
    this.busy.set(stage.id);
    this.api.approveRunStage({ stageId: stage.id, decision, ...(comment ? { comment } : {}), answers }, { runId: run.id }).subscribe({
      next: () => {
        this.busy.set(null);
        this.comment.set('');
        this.answers.set({});
        this.rejectTried.set(false);
        this.messages.add({ severity: decision === 'approve' ? 'success' : 'warn', summary: decision === 'approve' ? 'Этап принят' : 'Этап перезапущен с вашим замечанием', life: 4000 });
      },
      error: () => this.busy.set(null), // текст ошибки (409/422) покажет перехватчик ошибок
    });
  }
}
