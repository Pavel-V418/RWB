// Общие шаги e2e-сценариев (каркас, не зависит от предметной области).
//
// Вход:
//   • настоящий backend — учётные записи из окружения:
//       E2E_USERS='{"<роль>": {"login": "...", "password": "..."}, ...}'  — по одной на роль
//       или E2E_LOGIN / E2E_PASSWORD — одна учётная запись на все сценарии;
//     вход идёт через API входа, сессия кладётся туда же, где её хранит приложение;
//   • без учётных записей (локальный mock-стенд) — сессия с нужными ролями без запроса к серверу.
import { APIRequestContext, Page, expect } from '@playwright/test';

export interface FieldPlan {
  name: string;
  kind: string;
  required: boolean;
  value?: string | number;
  marker?: boolean;
  minLength?: number;
  maxLength?: number;
  skip?: string;
  fromApi?: boolean;
}

export interface ApiCfg {
  apiBase: string;
  loginPath: string | null;
}

type Creds = { login: string; password: string };

function users(): Record<string, Creds> {
  try {
    return JSON.parse(process.env['E2E_USERS'] ?? '{}') as Record<string, Creds>;
  } catch {
    return {};
  }
}

/** Тесты идут против настоящего backend (заданы учётные записи). */
export function realBackend(): boolean {
  return !!process.env['E2E_LOGIN'] || Object.keys(users()).length > 0;
}

/**
 * Войти пользователем с одной из ролей. strictRole — нужна именно эта роль (проверка запрета доступа):
 * тогда одной общей учётки E2E_LOGIN недостаточно. → null или причина пропуска теста.
 */
export async function signIn(page: Page, request: APIRequestContext, cfg: ApiCfg, roles: readonly string[], strictRole = false): Promise<string | null> {
  const all = users();
  const role = roles.find((r) => all[r]);
  let creds: Creds | null = role ? all[role] : null;
  if (!creds && !strictRole && process.env['E2E_LOGIN']) {
    creds = { login: process.env['E2E_LOGIN'] ?? '', password: process.env['E2E_PASSWORD'] ?? '' };
  }
  let session: unknown;
  if (creds && cfg.loginPath) {
    const r = await request.post(cfg.apiBase + cfg.loginPath, { data: creds });
    expect(r.ok(), `вход ${creds.login}: HTTP ${r.status()}`).toBeTruthy();
    const b = (await r.json()) as { accessToken: string; expiresIn?: number; user: unknown };
    session = { token: b.accessToken, expiresAt: Date.now() + (b.expiresIn ?? 3600) * 1000, user: b.user };
  } else if (realBackend()) {
    return cfg.loginPath ? `нет учётной записи для роли ${roles.join(' / ')} в E2E_USERS` : null;
  } else {
    session = { token: 'e2e', expiresAt: Date.now() + 3600_000, user: { id: 'e2e', name: 'E2E', roles: [...roles] } };
  }
  await page.addInitScript((s) => sessionStorage.setItem('sf.session', s), JSON.stringify(session));
  return null;
}

/** Уникальная метка прогона: по ней сценарий находит созданную запись в списке. */
export function runMarker(): string {
  return `E2E-${Date.now().toString(36)}-${Math.floor(Math.random() * 1e4)}`;
}

/** Заполнить поле формы по плану генератора. → значение, которое ввели (для текстовых полей). */
export async function fillField(page: Page, screen: string, f: FieldPlan, marker: string): Promise<string | null> {
  const el = page.getByTestId(`${screen}-field-${f.name}`);
  switch (f.kind) {
    case 'text':
    case 'textarea': {
      let v = f.marker ? marker : String(f.value ?? '');
      if (f.marker) {
        while (v.length < (f.minLength ?? 0)) v += '-';
        v = v.slice(0, f.maxLength ?? 200);
      }
      await el.fill(v);
      return v;
    }
    case 'password':
    case 'number':
      await el.locator('input').first().fill(String(f.value ?? ''));
      return null;
    case 'date':
    case 'datetime': {
      const input = el.locator('input').first();
      await input.fill(String(f.value ?? ''));
      await input.press('Escape');
      return null;
    }
    case 'select':
    case 'multiselect': {
      await el.click();
      const option = page.getByRole('option').first();
      await expect(option, `нет вариантов в поле ${f.name}`).toBeVisible();
      await option.click();
      if (f.kind === 'multiselect') await page.keyboard.press('Escape');
      return null;
    }
    default:
      return null;
  }
}

export function apiPath(cfg: ApiCfg, pathRegex: string): RegExp {
  return new RegExp(`${cfg.apiBase.replace(/[/.]/g, '\\$&')}${pathRegex}$`);
}
