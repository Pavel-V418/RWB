import { registerLocaleData } from '@angular/common';
import localeRu from '@angular/common/locales/ru';

import { fmtCoords, fmtDuration, fmtRelative, formatValue, interpolate } from './format';

registerLocaleData(localeRu);

describe('format', () => {
  it('duration', () => {
    expect(fmtDuration(3900)).toBe('1 ч 05 мин');
    expect(fmtDuration(600)).toBe('10 мин');
    expect(fmtDuration(40)).toBe('40 с');
    expect(fmtDuration(-5)).toBe('истекло');
  });
  it('relative', () => {
    const now = Date.parse('2026-10-30T12:00:00Z');
    expect(fmtRelative('2026-10-30T11:59:50Z', now)).toBe('только что');
    expect(fmtRelative('2026-10-30T11:50:00Z', now)).toBe('10 мин назад');
  });
  it('coordinates lat, lon', () => {
    expect(fmtCoords({ type: 'Point', coordinates: [12.5, 41.9] })).toBe('41.90000, 12.50000');
  });
  it('status label', () => {
    expect(formatValue('active', 'status', { active: { label: 'Активна', severity: 'success' } })).toBe('Активна');
  });
  it('interpolate with format', () => {
    expect(interpolate('Объект {payload.code}', { payload: { code: '42' } })).toBe('Объект 42');
    expect(interpolate('до {t:time}', { t: '2026-10-30T12:20:00' })).toBe('до 12:20');
  });
});
