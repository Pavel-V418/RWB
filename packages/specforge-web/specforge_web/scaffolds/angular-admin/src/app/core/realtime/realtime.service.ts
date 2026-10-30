import { Signal } from '@angular/core';
import { Observable } from 'rxjs';

import { EventEnvelope } from './event-envelope';

export type RealtimeStatus = 'connecting' | 'online' | 'offline' | 'disabled';

/** Абстракция realtime-канала. Реализации: SignalR (прод) и Mock (dev/демо/тесты). */
export abstract class RealtimeService {
  abstract readonly status: Signal<RealtimeStatus>;
  /** Поток событий указанных типов ('*' — все). Подписка лениво поднимает соединение. */
  abstract on(types: readonly string[]): Observable<EventEnvelope>;
  /** Срабатывает после восстановления соединения — экраны перечитывают данные через REST. */
  abstract readonly reconnected$: Observable<void>;
}
