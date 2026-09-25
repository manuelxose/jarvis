import { defineConfig, devices } from '@playwright/test'

/**
 * Visual QA infrastructure (Phase 1 scope: prove the pipeline works, not
 * cover final visual design). Desktop-only viewports per section 16 —
 * mobile is not a Jarvis target.
 */
export default defineConfig({
  testDir: './src/testing',
  fullyParallel: true,
  // ponytail: capped — screenshot capture under >4 concurrent workers on this
  // WSL host showed occasional single-pixel paint-timing flakes (observed on
  // 'speaking' state, passed on isolated re-run). Ceiling: if flakes persist
  // at 4, move to serial for the *-screenshot tests specifically.
  workers: 4,
  reporter: 'list',
  use: {
    baseURL: 'http://localhost:4173',
    // Deterministic screenshots and a standing check that the reduced-motion
    // path (section 13/14) never breaks layout or interaction.
    reducedMotion: 'reduce',
  },
  webServer: {
    command: 'npm run preview -- --port 4173',
    url: 'http://localhost:4173',
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
  projects: [
    {
      name: 'desktop-1080p',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1920, height: 1080 } },
    },
    {
      name: 'desktop-1440p',
      use: { ...devices['Desktop Chrome'], viewport: { width: 2560, height: 1440 } },
    },
  ],
})
