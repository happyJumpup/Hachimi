import { defineConfig, devices } from '@playwright/test'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const webRoot = fileURLToPath(new URL('.', import.meta.url))
const projectRoot = path.resolve(webRoot, '../..')
const sourcePath = path.join(projectRoot, 'tmp', 'e2e-source.mp4')

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: 'http://127.0.0.1:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  projects: [
    {
      name: 'mobile-chromium',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 390, height: 844 },
      },
    },
  ],
  webServer: [
    {
      command: 'uv run --project ../../services/analysis-api uvicorn hakimi_analysis.main:app --host 127.0.0.1 --port 8000',
      cwd: webRoot,
      url: 'http://127.0.0.1:8000/api/v1/health',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        ...process.env,
        APP_ENV: 'test',
        ANALYSIS_PROVIDER: 'test',
        HAKIMI_DEMO_VIDEO_PATH: sourcePath,
      },
    },
    {
      command: 'pnpm dev --host 127.0.0.1 --port 5173',
      cwd: webRoot,
      url: 'http://127.0.0.1:5173',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
})
