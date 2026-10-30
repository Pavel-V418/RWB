import { DestroyRef, inject, signal } from '@angular/core';
import { Observable, Subscription } from 'rxjs';

import { Problem, toProblem } from '../../core/http/problem';

export type ViewStatus = 'loading' | 'empty' | 'error' | 'forbidden' | 'ready';

export function isEmptyValue(v: unknown): boolean {
  if (v === null || v === undefined) return true;
  if (Array.isArray(v)) return v.length === 0;
  if (typeof v === 'object' && Array.isArray((v as { items?: unknown }).items)) {
    return (v as { items: unknown[] }).items.length === 0;
  }
  return false;
}

/**
 * Источник данных экрана с явным статусом. Маппинг ошибок:
 * 403 → forbidden, 404 → empty, остальное → error. Повторная загрузка с silent=true
 * не сбрасывает экран в «загрузку» (realtime-обновления не мигают).
 */
export class Loadable<T> {
  readonly data = signal<T | null>(null);
  readonly status = signal<ViewStatus>('loading');
  readonly problem = signal<Problem | null>(null);
  /** Идёт любой запрос, включая тихий (индикатор в таблице, а не скелетон). */
  readonly busy = signal(false);
  private sub: Subscription | null = null;

  constructor(
    private readonly loader: () => Observable<T>,
    private readonly isEmpty: (v: T) => boolean = isEmptyValue,
    destroyRef?: DestroyRef,
  ) {
    destroyRef?.onDestroy(() => this.sub?.unsubscribe());
  }

  load(opts: { silent?: boolean } = {}): void {
    if (!opts.silent || this.data() === null) this.status.set('loading');
    this.sub?.unsubscribe();
    this.busy.set(true);
    this.sub = this.loader().subscribe({
      next: (v) => {
        this.busy.set(false);
        this.data.set(v);
        this.problem.set(null);
        this.status.set(this.isEmpty(v) ? 'empty' : 'ready');
      },
      error: (e: unknown) => {
        this.busy.set(false);
        const p = toProblem(e);
        this.problem.set(p);
        if (opts.silent && this.data() !== null && p.status !== 403) return; // оставляем последние данные
        this.status.set(p.status === 403 ? 'forbidden' : p.status === 404 ? 'empty' : 'error');
      },
    });
  }

  /** Локальное изменение данных (realtime patch/prepend) без запроса. */
  update(fn: (v: T) => T): void {
    const d = this.data();
    if (d !== null) {
      const next = fn(d);
      this.data.set(next);
      this.status.set(this.isEmpty(next) ? 'empty' : 'ready');
    }
  }
}

/** Создать Loadable в контексте внедрения (отписка при уничтожении компонента). */
export function loadable<T>(loader: () => Observable<T>, isEmpty?: (v: T) => boolean): Loadable<T> {
  return new Loadable<T>(loader, isEmpty, inject(DestroyRef));
}

/** Сводный статус экрана: доступ по ролям → статус основного источника. */
export function screenStatus(allowed: boolean, primary: ViewStatus): ViewStatus {
  return allowed ? primary : 'forbidden';
}
