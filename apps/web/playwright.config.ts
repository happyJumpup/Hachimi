import { defineConfig, devices } from '@playwright/test'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const webRoot = fileURLToPath(new URL('.', import.meta.url))
const projectRoot = path.resolve(webRoot, '../..')
const sourcePath = path.join(projectRoot, 'tmp', 'e2e-source.mp4')
const sourceRoot = path.join(projectRoot, 'tmp', 'e2e-sources')
const sourceManifestPath = path.join(projectRoot, 'tmp', 'e2e-source-manifest.json')

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: 'http://127.0.0.1:15173',
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
      command: 'uv run --project ../../services/analysis-api uvicorn hakimi_analysis.main:app --host 127.0.0.1 --port 18123',
      cwd: webRoot,
      url: 'http://127.0.0.1:18123/api/v1/health',
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        ...process.env,
        APP_ENV: 'test',
        ANALYSIS_PROVIDER: 'test',
        CORS_ORIGINS: 'http://127.0.0.1:15173',
        HAKIMI_DEMO_VIDEO_PATH: sourcePath,
        SOURCE_MANIFEST_PATH: sourceManifestPath,
        SOURCE_MEDIA_ROOT: sourceRoot,
        PUBLIC_MEDIA_BASE_URL: 'https://127.0.0.1:18123/e2e-media',
      },
    },
    {
      command: 'pnpm dev --host 127.0.0.1 --port 15173',
      cwd: webRoot,
      url: 'http://127.0.0.1:15173',
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        ...process.env,
        VITE_API_TARGET: 'http://127.0.0.1:18123',
      },
    },
  ],
})
