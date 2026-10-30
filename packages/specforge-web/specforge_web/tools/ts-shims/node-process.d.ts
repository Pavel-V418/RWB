// process.env для ts-check e2e-файлов (в Playwright он есть из Node.js).
declare var process: { env: Record<string, string | undefined> };
