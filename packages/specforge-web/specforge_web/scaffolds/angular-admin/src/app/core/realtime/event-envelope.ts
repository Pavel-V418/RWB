/** contracts/realtime/event-envelope.schema.json */
export interface EventEnvelope<P = Record<string, unknown>> {
  id: string;
  type: string;
  version: number;
  occurredAt: string;
  source: string;
  correlationId?: string;
  subject?: { entity: string; id: string };
  payload: P;
}

/** Событие → строка списка/журнала: поля payload называются как поля REST-ресурса. */
export function envelopeToItem(e: EventEnvelope): Record<string, unknown> {
  return { id: e.id, type: e.type, occurredAt: e.occurredAt, ...e.payload };
}
