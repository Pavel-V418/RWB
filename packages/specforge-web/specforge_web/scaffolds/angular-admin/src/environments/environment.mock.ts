import { AppEnvironment } from './environment';

// Режим разработки против Prism: REST из мока, события — из файла.
export const environment: AppEnvironment = {
  apiBaseUrl: '/api/v1',
  hubUrl: '/hubs/events',
  realtime: 'mock',
  mapStyleUrl: null,
  mapCenter: [0, 30],
  mapZoom: 1.5,
  mockEventsUrl: 'assets/mock-events.json',
};
