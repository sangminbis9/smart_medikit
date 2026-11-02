import { defineConfig, devices } from '@playwright/test';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const apiMode = process.env.MEDKIT_E2E_API === '1';
const baseURL = apiMode ? 'http://127.0.0.1:4174' : 'http://127.0.0.1:4173';

export default defineConfig({
  testDir: './e2e',
  testMatch: apiMode ? '**/care-api.spec.ts' : '**/!(care-api).spec.ts',
  fullyParallel: false,
  retries: 0,
  reporter: 'list',
  outputDir: join(tmpdir(), 'medikit-playwright'),
  use: {
    baseURL,
    trace: 'off',
    screenshot: 'off',
    channel: 'chrome',
  },
  projects: [
    {
      name: 'desktop',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1440, height: 960 },
      },
    },
    {
      name: 'mobile',
      use: { ...devices['iPhone 13'], defaultBrowserType: 'chromium' },
    },
  ],
  webServer: {
    command: apiMode
      ? 'npm run dev -- --port 4174 --strictPort'
      : 'npm run preview -- --port 4173 --strictPort',
    url: baseURL,
    env: apiMode
      ? { VITE_API_URL: `${baseURL}/api`, VITE_BOX_ID: 'test-box' }
      : {},
    reuseExistingServer: !process.env.CI,
  },
});
