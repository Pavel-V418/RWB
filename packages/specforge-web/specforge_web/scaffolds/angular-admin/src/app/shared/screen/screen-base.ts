import { DestroyRef, Directive, OnInit, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';
import { ConfirmationService, MessageService } from 'primeng/api';
import { Observable, Subject, debounceTime } from 'rxjs';

import { AuthService } from '../../core/auth/auth.service';
import { newIdempotencyKey } from '../../core/http/idempotency';
import { EventEnvelope } from '../../core/realtime/event-envelope';
import { RealtimeService } from '../../core/realtime/realtime.service';
import { ApiService } from '../../generated/api/api.service';
import { interpolate } from '../value/format';
import { ViewStatus } from '../state/loadable';

export interface ScreenAction {
  confirm?: string;
  successMessage?: string;
  idempotent?: boolean;
  call: (opts: { idempotencyKey?: string }) => Observable<unknown>;
  after?: () => void;
}

export interface NotificationRule {
  eventType: string;
  text: string;
  severity: 'success' | 'info' | 'warn' | 'danger';
}

/**
 * Общая логика сгенерированных экранов. Генератор создаёт наследника с источниками данных
 * (Loadable), шаблоном и описанием realtime; сюда вынесено всё, что одинаково для всех экранов.
 */
@Directive()
export abstract class ScreenBase implements OnInit {
  protected readonly api = inject(ApiService);
  protected readonly auth = inject(AuthService);
  protected readonly router = inject(Router);
  protected readonly route = inject(ActivatedRoute);
  protected readonly messages = inject(MessageService);
  protected readonly confirmation = inject(ConfirmationService);
  protected readonly realtime = inject(RealtimeService);
  protected readonly destroyRef = inject(DestroyRef);

  /** Роли из screen-spec. */
  protected abstract readonly roles: readonly string[];
  /** Типы событий из screen-spec.realtime (пусто — без realtime). */
  protected readonly realtimeTypes: readonly string[] = [];
  protected readonly notificationRules: readonly NotificationRule[] = [];

  protected readonly routeParams = signal<Record<string, string>>({});
  protected readonly allowed = computed(() => this.auth.hasAnyRole(this.roles));
  protected readonly running = signal<string | null>(null);
  private readonly refresh$ = new Subject<void>();

  /** Загрузить все источники экрана. silent — без скелетона. */
  protected abstract reload(silent?: boolean): void;
  /** Обработать событие (prepend/patch). По умолчанию — тихая перезагрузка. */
  protected onRealtime(_e: EventEnvelope): void {
    this.scheduleRefresh();
  }

  ngOnInit(): void {
    this.route.paramMap.pipe(takeUntilDestroyed(this.destroyRef)).subscribe((pm) => {
      const params: Record<string, string> = {};
      pm.keys.forEach((k) => (params[k] = pm.get(k) ?? ''));
      this.routeParams.set(params);
      if (this.allowed()) this.reload();
    });
    this.refresh$.pipe(debounceTime(500), takeUntilDestroyed(this.destroyRef)).subscribe(() => this.reload(true));
    if (this.realtimeTypes.length && this.allowed()) {
      this.realtime.on(this.realtimeTypes).pipe(takeUntilDestroyed(this.destroyRef)).subscribe((e) => {
        this.notify(e);
        this.onRealtime(e);
      });
      this.realtime.reconnected$.pipe(takeUntilDestroyed(this.destroyRef)).subscribe(() => this.reload(true));
    }
  }

  protected status(primary: ViewStatus): ViewStatus {
    return this.allowed() ? primary : 'forbidden';
  }

  protected routeParam(name: string): string {
    return this.routeParams()[name] ?? '';
  }

  protected context(key: string): string {
    const v = this.auth.context(key);
    return v === null || v === undefined ? '' : String(v);
  }

  protected scheduleRefresh(): void {
    this.refresh$.next();
  }

  protected can(roles: readonly string[] | undefined): boolean {
    return this.auth.hasAnyRole(roles);
  }

  protected go(path: (string | number)[]): void {
    void this.router.navigate(path);
  }

  protected openExternal(url: string): void {
    if (url) window.open(url, '_blank', 'noopener');
  }

  /** Действие с подтверждением, Idempotency-Key, обратной связью и перезагрузкой. */
  protected run(id: string, action: ScreenAction): void {
    const exec = () => {
      const key = action.idempotent ? newIdempotencyKey() : undefined;
      this.running.set(id);
      action.call({ idempotencyKey: key }).subscribe({
        next: () => {
          this.running.set(null);
          if (action.successMessage) this.messages.add({ severity: 'success', summary: action.successMessage, life: 4000 });
          (action.after ?? (() => this.reload(true)))();
        },
        error: () => this.running.set(null), // toast показывает errorInterceptor
      });
    };
    if (action.confirm) {
      this.confirmation.confirm({
        header: 'Подтверждение',
        message: action.confirm,
        icon: 'pi pi-exclamation-triangle',
        acceptLabel: 'Да',
        rejectLabel: 'Отмена',
        acceptButtonProps: { severity: 'danger' },
        rejectButtonProps: { severity: 'secondary', outlined: true },
        accept: exec,
      });
    } else {
      exec();
    }
  }

  private notify(e: EventEnvelope): void {
    const rule = this.notificationRules.find((r) => r.eventType === e.type);
    if (rule) {
      this.messages.add({ severity: rule.severity === 'danger' ? 'error' : rule.severity, summary: interpolate(rule.text, e), life: 6000 });
    }
  }
}
