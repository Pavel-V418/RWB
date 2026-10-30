import { Injectable, OnDestroy, inject, signal } from '@angular/core';
import { HubConnection, HubConnectionBuilder, HubConnectionState, LogLevel } from '@microsoft/signalr';
import { Observable, Subject, filter, finalize, share } from 'rxjs';

import { environment } from '../../../environments/environment';
import { AuthService } from '../auth/auth.service';
import { EventEnvelope } from './event-envelope';
import { RealtimeService, RealtimeStatus } from './realtime.service';

const DEDUP_WINDOW = 500;

@Injectable()
export class SignalrRealtimeService extends RealtimeService implements OnDestroy {
  private readonly auth = inject(AuthService);
  readonly status = signal<RealtimeStatus>('offline');
  private readonly events$ = new Subject<EventEnvelope>();
  private readonly reconnectedSubject = new Subject<void>();
  readonly reconnected$ = this.reconnectedSubject.asObservable();
  private connection: HubConnection | null = null;
  private readonly seen: string[] = [];
  private readonly typeRefs = new Map<string, number>();

  on(types: readonly string[]): Observable<EventEnvelope> {
    this.ensureConnected();
    types.forEach((t) => this.retain(t));
    const wanted = new Set(types);
    return this.events$.pipe(
      filter((e) => wanted.has('*') || wanted.has(e.type)),
      finalize(() => types.forEach((t) => this.release(t))),
      share(),
    );
  }

  private ensureConnected(): void {
    if (this.connection) return;
    this.connection = new HubConnectionBuilder()
      .withUrl(environment.hubUrl, { accessTokenFactory: () => this.auth.token() ?? '' })
      .withAutomaticReconnect([0, 2000, 5000, 10000, 30000])
      .configureLogging(LogLevel.Warning)
      .build();
    this.connection.on('event', (e: EventEnvelope) => this.emit(e));
    this.connection.onreconnecting(() => this.status.set('connecting'));
    this.connection.onreconnected(() => {
      this.status.set('online');
      void this.resubscribe();
      this.reconnectedSubject.next();
    });
    this.connection.onclose(() => this.status.set('offline'));
    this.status.set('connecting');
    this.connection
      .start()
      .then(() => {
        this.status.set('online');
        return this.resubscribe();
      })
      .catch(() => this.status.set('offline'));
  }

  private emit(e: EventEnvelope): void {
    if (this.seen.includes(e.id)) return;
    this.seen.push(e.id);
    if (this.seen.length > DEDUP_WINDOW) this.seen.shift();
    this.events$.next(e);
  }

  private retain(type: string): void {
    const n = (this.typeRefs.get(type) ?? 0) + 1;
    this.typeRefs.set(type, n);
    if (n === 1 && this.connection?.state === HubConnectionState.Connected) {
      void this.connection.invoke('Subscribe', [type]).catch(() => undefined);
    }
  }

  private release(type: string): void {
    const n = (this.typeRefs.get(type) ?? 1) - 1;
    if (n <= 0) {
      this.typeRefs.delete(type);
      if (this.connection?.state === HubConnectionState.Connected) {
        void this.connection.invoke('Unsubscribe', [type]).catch(() => undefined);
      }
    } else {
      this.typeRefs.set(type, n);
    }
  }

  private async resubscribe(): Promise<void> {
    const types = [...this.typeRefs.keys()];
    if (types.length && this.connection?.state === HubConnectionState.Connected) {
      await this.connection.invoke('Subscribe', types).catch(() => undefined);
    }
  }

  ngOnDestroy(): void {
    void this.connection?.stop();
  }
}
