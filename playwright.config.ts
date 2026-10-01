import base from './ui/playwright.config.ts'

// `npm --prefix ui exec -- playwright test` keeps the cwd at the repo root, where the
// UI config is not discovered; re-anchor its relative paths on ui/.
export default {
  ...base,
  testDir: './ui/src/testing',
  outputDir: './ui/test-results',
  webServer: { ...base.webServer, cwd: './ui' },
}
