import { chromium } from 'playwright';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const dir = path.dirname(fileURLToPath(import.meta.url));
const files = ['direction-a-cinematic-command', 'direction-b-precision-workstation', 'direction-c-adaptive-environment'];

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
for (const f of files) {
  await page.goto(`file://${dir}/${f}.html`);
  await page.screenshot({ path: `${dir}/../docs/design/screenshots/${f}.png` });
}
await browser.close();
console.log('done');
