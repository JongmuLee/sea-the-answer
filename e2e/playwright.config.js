// End-to-end tests. They drive the real UI → Next.js proxy → FastAPI → PostgreSQL.
// The workspace must start EMPTY (the flow loads the sample data and checks counts):
//   docker compose --profile e2e up --build --force-recreate --exit-code-from e2e e2e   (recommended, isolated DB)
//   docker compose --profile e2e down                                                      (afterwards)
//   E2E_BASE_URL=http://127.0.0.1:5173 npx playwright test           (against a fresh local stack)
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: !!process.env.CI,
  timeout: 5 * 60_000,
  expect: { timeout: 15_000 },
  reporter: [['list'], ['html', { open: 'never' }]],
  outputDir: 'test-results',
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://127.0.0.1:5173',
    locale: 'ko-KR',
    viewport: { width: 1440, height: 1000 },
    actionTimeout: 15_000,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1440, height: 1000 },
        // Optional: use an already-installed Chrome/Chromium instead of `npx playwright install`.
        ...(process.env.CHROME_PATH ? { launchOptions: { executablePath: process.env.CHROME_PATH } } : {}),
      },
    },
  ],
});
