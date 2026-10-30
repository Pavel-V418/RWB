// Значения по умолчанию. В контейнере переопределяются через /assets/env.js (window.__env),
// который nginx генерирует из переменных окружения при старте — без пересборки образа.
export interface AppEnvironment {
  apiBaseUrl: string;
  hubUrl: string;
  realtime: 'signalr' | 'mock' | 'off';
  mapStyleUrl: string | null;
  /** Центр карты по умолчанию [lon, lat], если экран его не задаёт и данных ещё нет. */
  mapCenter: [number, number];
  mapZoom: number;
  mockEventsUrl: string;
}

declare global {
  interface Window { __env?: Partial<AppEnvironment>; }
}

const defaults: AppEnvironment = {
  apiBaseUrl: '/api/v1',
  hubUrl: '/hubs/events',
  realtime: 'signalr',
  mapStyleUrl: null, // null → встроенный стиль с растровыми тайлами OSM
  mapCenter: [0, 30], // нейтральный обзор; с данными карта сама подстраивается под объекты (fitToData)
  mapZoom: 1.5,
  mockEventsUrl: 'assets/mock-events.json',
};

export const environment: AppEnvironment = { ...defaults, ...(typeof window !== 'undefined' ? window.__env : {}) };
