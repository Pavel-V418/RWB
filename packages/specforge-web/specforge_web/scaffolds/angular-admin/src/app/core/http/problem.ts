import { HttpContextToken, HttpErrorResponse } from '@angular/common/http';

/** RFC 7807 — единый формат ошибок (contracts/api/api-conventions.md §4). */
export interface Problem {
  type?: string;
  title: string;
  status: number;
  code?: string;
  detail?: string;
  traceId?: string;
  errors?: Record<string, string[]>;
}

/** Запрос сам показывает ошибку (форма, экран) — interceptor не дублирует toast. */
export const SILENT_ERRORS = new HttpContextToken<boolean>(() => false);

export function toProblem(err: unknown): Problem {
  if (err instanceof HttpErrorResponse) {
    const body = err.error;
    if (body && typeof body === 'object' && 'title' in body) {
      return { status: err.status, ...(body as Partial<Problem>), title: String((body as Problem).title) };
    }
    if (err.status === 0) return { status: 0, title: 'Нет связи с сервером', detail: 'Проверьте подключение к сети' };
    return { status: err.status, title: err.statusText || 'Ошибка запроса' };
  }
  return { status: -1, title: 'Непредвиденная ошибка', detail: err instanceof Error ? err.message : String(err) };
}

export function problemMessage(p: Problem): string {
  if (p.status === 429) return 'Слишком много запросов. Повторите через несколько секунд';
  if (p.status === 403) return 'Недостаточно прав для этого действия';
  if (p.status === 409) return p.detail ?? 'Конфликт: данные уже изменены';
  return p.detail ?? p.title;
}
