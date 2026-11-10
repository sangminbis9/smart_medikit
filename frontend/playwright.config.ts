import { defineConfig, devices } from '@playwright/test';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const apiMode = process.env.MEDKIT_E2E_API === '1';
const activityMode = process.env.MEDKIT_E2E_ACTIVITY === '1';
const publicMedicationMode = process.env.MEDKIT_E2E_MEDICATION_PUBLIC === '1';
const medicationMode =
  process.env.MEDKIT_E2E_MEDICATION === '1' || publicMedicationMode;
const baseURL = activityMode
  ? 'http://127.0.0.1:4176'
  : medicationMode
    ? 'http://127.0.0.1:4175'
    : apiMode
      ? 'http://127.0.0.1:4174'
      : 'http://127.0.0.1:4173';

export default defineConfig({
  testDir: './e2e',
  testMatch: activityMode
    ? '**/activity.spec.ts'
    : medicationMode
      ? '**/medication.spec.ts'
      : apiMode
        ? '**/care-api.spec.ts'
        : '**/!(care-api|medication|activity).spec.ts',
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
    command: activityMode
      ? 'npm run dev -- --port 4176 --strictPort'
      : medicationMode
        ? 'npm run dev -- --port 4175 --strictPort'
        : apiMode
          ? 'npm run dev -- --port 4174 --strictPort'
          : 'npm run preview -- --port 4173 --strictPort',
    url: baseURL,
    env: activityMode
      ? {
          VITE_API_URL: `${baseURL}/api`,
          VITE_BOX_ID: 'test-box',
          VITE_ACTIVITY_API_URL: 'https://activity.test',
        }
      : medicationMode
        ? {
            VITE_MEDICATION_API_URL: `${baseURL}/medication-api`,
            VITE_MEDICATION_PUBLIC_DEMO: publicMedicationMode
              ? 'true'
              : 'false',
            VITE_API_URL: '',
            VITE_BOX_ID: 'test-box',
          }
        : apiMode
          ? { VITE_API_URL: `${baseURL}/api`, VITE_BOX_ID: 'test-box' }
          : {},
    reuseExistingServer: !process.env.CI,
  },
});
