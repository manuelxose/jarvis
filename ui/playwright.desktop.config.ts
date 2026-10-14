import { defineConfig, devices } from '@playwright/test'

// Synthetic CSS viewport coverage only; native Windows/DPI acceptance is separate.
export default defineConfig({
  testDir: './src/testing',
  testMatch: 'desktop-command-center.spec.ts',
  fullyParallel: true,
  workers: 3,
  reporter: 'list',
  timeout: 30_000,
  use: {
    baseURL: 'http://localhost:4173',
    reducedMotion: 'reduce',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'npm run dev -- --host localhost --port 4173 --strictPort',
    url: 'http://localhost:4173',
    reuseExistingServer: false,
    timeout: 30_000,
  },
  projects: [
    { name: 'css-1080p', use: { ...devices['Desktop Chrome'], viewport: { width: 1920, height: 1080 } } },
    { name: 'css-1440p', use: { ...devices['Desktop Chrome'], viewport: { width: 2560, height: 1440 } } },
    { name: 'css-4k', use: { ...devices['Desktop Chrome'], viewport: { width: 3840, height: 2160 } } },
  ],
})
