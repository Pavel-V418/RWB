import { formatDate, formatNumber } from '@angular/common';

export type ValueFormat =
  | 'text' | 'number' | 'currency' | 'datetime' | 'date' | 'time' | 'duration'
  | 'relative-time' | 'boolean' | 'status' | 'link' | 'coordinates';

export type Severity = 'success' | 'info' | 'warn' | 'danger' | 'secondary' | 'contrast';
/** Значение → подпись/цвет. Partial: ключа может не быть (неизвестный статус от API). */
export type StatusMap = Partial<Record<string, { label: string; severity: Severity }>>;

const LOCALE = 'ru-RU';

export function isBlank(v: unknown): boolean {
  return v === null || v === undefined || v === '';
}

export function getPath(obj: unknown, path: string): unknown {
  return path.split('.').reduce<unknown>((o, k) => (o && typeof o === 'object' ? (o as Record<string, unknown>)[k] : undefined), obj);
}

export function fmtDate(v: unknown, kind: 'datetime' | 'date' | 'time' = 'datetime'): string {
  if (isBlank(v)) return '';
  const pattern = { datetime: 'dd.MM.yyyy HH:mm', date: 'dd.MM.yyyy', time: 'HH:mm' }[kind];
  try {
    return formatDate(v as string | number | Date, pattern, LOCALE);
  } catch {
    return String(v);
  }
}

/** Секунды → «1 ч 05 мин», «12 мин», «40 с». Отрицательное → «истекло». */
export function fmtDuration(v: unknown): string {
  if (isBlank(v)) return '';
  const s = Math.round(Number(v));
  if (Number.isNaN(s)) return String(v);
  if (s <= 0) return 'истекло';
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return `${h} ч ${String(m).padStart(2, '0')} мин`;
  if (m > 0) return `${m} мин`;
  return `${s} с`;
}

export function fmtRelative(v: unknown, now: number = Date.now()): string {
  if (isBlank(v)) return '';
  const t = new Date(v as string).getTime();
  if (Number.isNaN(t)) return String(v);
  const diff = Math.round((now - t) / 1000);
  if (diff < 45) return 'только что';
  if (diff < 3600) return `${Math.round(diff / 60)} мин назад`;
  if (diff < 86400) return `${Math.round(diff / 3600)} ч назад`;
  return fmtDate(v, 'datetime');
}

export function fmtCoords(v: unknown): string {
  const c = Array.isArray(v) ? v : (v as { coordinates?: unknown })?.coordinates;
  if (!Array.isArray(c) || c.length < 2) return '';
  const [lon, lat] = c as number[];
  return `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
}

export function fmtNumber(v: unknown, digits = '1.0-2'): string {
  if (isBlank(v)) return '';
  const n = Number(v);
  return Number.isNaN(n) ? String(v) : formatNumber(n, LOCALE, digits);
}

export function fmtCurrency(v: unknown): string {
  const s = fmtNumber(v, '1.0-2');
  return s ? `${s} ₽` : '';
}

/** Текстовое представление для всплывающих окон карты и экспорта. */
export function formatValue(v: unknown, format: ValueFormat = 'text', statusMap?: StatusMap): string {
  if (isBlank(v)) return '';
  switch (format) {
    case 'datetime':
    case 'date':
    case 'time':
      return fmtDate(v, format);
    case 'duration':
      return fmtDuration(v);
    case 'relative-time':
      return fmtRelative(v);
    case 'number':
      return fmtNumber(v);
    case 'currency':
      return fmtCurrency(v);
    case 'boolean':
      return v ? 'Да' : 'Нет';
    case 'coordinates':
      return fmtCoords(v);
    case 'status':
      return statusMap?.[String(v)]?.label ?? String(v);
    default:
      return String(v);
  }
}

/** Подстановка {path} / {path:format} в шаблоны текстов (уведомления, ссылки). */
export function interpolate(template: string, ctx: unknown): string {
  return template.replace(/\{([A-Za-z0-9_.]+)(?::([a-z-]+))?\}/g, (_m, path: string, fmt?: string) =>
    formatValue(getPath(ctx, path), (fmt as ValueFormat) ?? 'text'),
  );
}

const SEVERITIES = new Set(['success', 'info', 'warn', 'danger', 'secondary', 'contrast']);

/** Значение поля severity из API → допустимый severity для p-tag. */
export function toSeverity(v: unknown, map?: StatusMap): Severity {
  const mapped = map?.[String(v)]?.severity;
  if (mapped) return mapped;
  return SEVERITIES.has(String(v)) ? (String(v) as Severity) : 'secondary';
}
