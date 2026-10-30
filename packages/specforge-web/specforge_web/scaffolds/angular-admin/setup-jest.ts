import { setupZoneTestEnv } from 'jest-preset-angular/setup-env/zone';

setupZoneTestEnv();

// jsdom не умеет matchMedia — нужен shell и app-map
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false, media: query, onchange: null,
    addEventListener: () => undefined, removeEventListener: () => undefined,
    addListener: () => undefined, removeListener: () => undefined, dispatchEvent: () => false,
  }),
});

// jsdom: нет ResizeObserver (нужен p-tabs, app-map) и отражения ARIA-свойства role (pSortableColumn)
class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver ??= ResizeObserverStub;
if (!('role' in HTMLElement.prototype)) {
  Object.defineProperty(HTMLElement.prototype, 'role', {
    configurable: true,
    get(this: HTMLElement) { return this.getAttribute('role'); },
    set(this: HTMLElement, v: string | null) { if (v === null) this.removeAttribute('role'); else this.setAttribute('role', v); },
  });
}
