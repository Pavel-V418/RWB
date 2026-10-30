import { EnvironmentProviders, Injectable, makeEnvironmentProviders, signal } from '@angular/core';
import { EMPTY, Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { EventEnvelope } from './event-envelope';
import { MockRealtimeService } from './mock-realtime.service';
import { RealtimeService, RealtimeStatus } from './realtime.service';
import { SignalrRealtimeService } from './signalr-realtime.service';

@Injectable()
export class DisabledRealtimeService extends RealtimeService {
  readonly status = signal<RealtimeStatus>('disabled');
  readonly reconnected$ = EMPTY as Observable<void>;
  on(): Observable<EventEnvelope> {
    return EMPTY;
  }
}

export function provideRealtime(): EnvironmentProviders {
  const impl = { signalr: SignalrRealtimeService, mock: MockRealtimeService, off: DisabledRealtimeService }[environment.realtime];
  return makeEnvironmentProviders([{ provide: RealtimeService, useClass: impl ?? DisabledRealtimeService }]);
}
