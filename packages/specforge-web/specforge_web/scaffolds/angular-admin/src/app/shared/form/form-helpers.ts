import { AbstractControl, FormGroup } from '@angular/forms';

import { Problem } from '../../core/http/problem';

/** Значения формы → тело запроса: Date → ISO, пустые строки → undefined. */
export function toApiBody<T extends object>(value: T): T {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(value)) {
    if (v === '' || v === null || v === undefined) continue;
    out[k] = v instanceof Date ? v.toISOString() : typeof v === 'string' ? v.trim() : v;
  }
  return out as T;
}

/** problem.errors → ошибки контролов {server: '...'} (RFC 7807, contracts §4). */
export function applyServerErrors(form: FormGroup, p: Problem): void {
  for (const [field, messages] of Object.entries(p.errors ?? {})) {
    const key = Object.keys(form.controls).find((k) => k.toLowerCase() === field.toLowerCase());
    if (key) {
      form.controls[key].setErrors({ server: messages.join('. ') });
      form.controls[key].markAsTouched();
    }
  }
}

export function showError(c: AbstractControl | null | undefined): boolean {
  return !!c && c.invalid && (c.touched || c.dirty);
}

export function errorText(c: AbstractControl | null | undefined, patternMessage?: string): string {
  const e = c?.errors;
  if (!e) return '';
  if (e['server']) return String(e['server']);
  if (e['required']) return 'Обязательное поле';
  if (e['minlength']) return `Минимум ${e['minlength'].requiredLength} символов`;
  if (e['maxlength']) return `Максимум ${e['maxlength'].requiredLength} символов`;
  if (e['pattern']) return patternMessage ?? 'Неверный формат';
  if (e['min']) return `Не меньше ${e['min'].min}`;
  if (e['max']) return `Не больше ${e['max'].max}`;
  return 'Неверное значение';
}
