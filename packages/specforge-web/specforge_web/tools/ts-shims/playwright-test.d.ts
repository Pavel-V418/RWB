// Минимальные типы @playwright/test для ts-check сгенерированных e2e (без node_modules).
export interface Request { method(): string; url(): string; }
export interface Response { status(): number; text(): Promise<string>; url(): string; request(): Request; ok(): boolean; json(): Promise<unknown>; }
export interface Locator {
  fill(v: string): Promise<void>;
  click(): Promise<void>;
  press(key: string): Promise<void>;
  first(): Locator;
  filter(o: { hasText?: string | RegExp }): Locator;
  locator(sel: string): Locator;
  or(other: Locator): Locator;
  count(): Promise<number>;
  isVisible(o?: { timeout?: number }): Promise<boolean>;
}
export interface Page {
  goto(url: string): Promise<unknown>;
  getByTestId(id: string): Locator;
  getByRole(role: string, o?: { name?: string | RegExp }): Locator;
  getByText(t: string | RegExp): Locator;
  locator(sel: string): Locator;
  on(ev: 'request', cb: (r: Request) => void): void;
  on(ev: 'console', cb: (m: { type(): string; text(): string }) => void): void;
  on(ev: 'pageerror', cb: (e: Error) => void): void;
  addInitScript<A>(fn: (arg: A) => void, arg?: A): Promise<void>;
  waitForResponse(pred: (r: Response) => boolean): Promise<Response>;
  keyboard: { press(key: string): Promise<void> };
}
export interface APIResponse { ok(): boolean; status(): number; json(): Promise<unknown>; text(): Promise<string>; }
export interface APIRequestContext { post(url: string, o?: { data?: unknown }): Promise<APIResponse>; }
type Fixtures = { page: Page; request: APIRequestContext };
type Body = (f: Fixtures) => Promise<void>;
interface TestFn {
  (title: string, body: Body): void;
  describe(title: string, body: () => void): void;
  skip(cond: boolean, reason?: string): void;
  beforeEach(body: Body): void;
}
export declare const test: TestFn;
interface Matchers {
  toBeVisible(): Promise<void>;
  toHaveCount(n: number): Promise<void>;
  toBeTruthy(): void;
  toBe(v: unknown): void;
  toEqual(v: unknown): void;
  toBeLessThan(n: number): void;
  toBeGreaterThan(n: number): void;
}
interface Expect {
  (v: unknown, message?: string): Matchers;
  poll(fn: () => Promise<number>, o?: { timeout?: number }): { toBeGreaterThan(n: number): Promise<void> };
}
export declare const expect: Expect;
export declare const devices: Record<string, Record<string, unknown>>;
export declare function defineConfig(c: unknown): unknown;
