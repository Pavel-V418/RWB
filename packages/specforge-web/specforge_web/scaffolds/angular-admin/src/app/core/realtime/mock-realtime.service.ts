import { HttpClient } from '@angular/common/http';
import { Injectable, OnDestroy, inject, signal } from '@angular/core';
import { EMPTY, Observable, Subject, Subscription, catchError, concatMap, delay, filter, from, of, share } from 'rxjs';

import { environment } from '../../../environments/environment';
import { EventEnvelope } from './event-envelope';
import { RealtimeService, RealtimeStatus } from './realtime.service';

/** Элемент файла mock-events.json: событие + пауза перед ним (мс). */
interface MockEvent extends Omit<EventEnvelope, 'occurredAt'> {
  delayMs?: number;
  occurredAt?: string;
}

/**
 * Проигрывает события из файла (по умолчанию assets/mock-events.json) по кругу.
 * occurredAt проставляется «сейчас», чтобы ленты выглядели живыми.
 */
@Injectable()
export class MockRealtimeService extends RealtimeService implements OnDestroy {
  private readonly http = inject(HttpClient);
  readonly status = signal<RealtimeStatus>('offline');
  readonly reconnected$ = EMPTY as Observable<void>;
  private readonly events$ = new Subject<EventEnvelope>();
  private player: Subscription | null = null;

  on(types: readonly string[]): Observable<EventEnvelope> {
    this.start();
    const wanted = new Set(types);
    return this.events$.pipe(filter((e) => wanted.has('*') || wanted.has(e.type)), share());
  }

  private start(): void {
    if (this.player) return;
    this.status.set('connecting');
    this.player = this.http
      .get<MockEvent[]>(environment.mockEventsUrl)
      .pipe(
        catchError(() => of([] as MockEvent[])),
        concatMap((list) => {
          this.status.set('online');
          return list.length ? this.loop(list) : EMPTY;
        }),
      )
      .subscribe((e) => this.events$.next(e));
  }

  private loop(list: MockEvent[]): Observable<EventEnvelope> {
    let round = 0;
    const play = (): Observable<EventEnvelope> =>
      from(list).pipe(
        concatMap((m) => of(m).pipe(delay(m.delayMs ?? 3000))),
        concatMap((m) => {
          const { delayMs: _d, ...rest } = m;
          return of({ ...rest, id: `${m.id}-${round}`, occurredAt: new Date().toISOString() } as EventEnvelope);
        }),
      );
    return new Observable<EventEnvelope>((sub) => {
      let inner: Subscription;
      const next = () => {
        inner = play().subscribe({ next: (e) => sub.next(e), complete: () => { round++; next(); } });
      };
      next();
      return () => inner?.unsubscribe();
    });
  }

  ngOnDestroy(): void {
    this.player?.unsubscribe();
  }
}
