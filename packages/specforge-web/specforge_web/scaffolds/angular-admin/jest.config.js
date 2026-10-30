/** @type {import('jest').Config} */
module.exports = {
  preset: 'jest-preset-angular',
  setupFilesAfterEnv: ['<rootDir>/setup-jest.ts'],
  testEnvironment: 'jsdom',
  testPathIgnorePatterns: ['/node_modules/', '/e2e/'],
  moduleNameMapper: {
    '^@app/(.*)$': '<rootDir>/src/app/$1',
    '^maplibre-gl$': '<rootDir>/src/testing/maplibre.mock.ts',
  },
  coverageReporters: ['text-summary', 'cobertura', 'html'],
  reporters: ['default'],
};
