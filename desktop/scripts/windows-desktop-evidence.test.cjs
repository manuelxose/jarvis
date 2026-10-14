const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const doc = fs.readFileSync(path.join(__dirname, '..', '..', 'docs', 'architecture', 'windows-desktop.md'), 'utf8');
const section = doc.slice(doc.indexOf('**Current re-run'), doc.indexOf('**Historical'));
const entries = [...section.matchAll(/^- `([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.stdout`:\s*(.*)$/gm)];

test('current re-run lists exactly five distinct evidence ids with purposes', () => {
  assert.ok(section.includes('2026-10-14'));
  assert.equal(entries.length, 5);
  assert.equal(new Set(entries.map((e) => e[1])).size, 5);
  for (const e of entries) assert.ok(e[2].trim().length > 10, `purpose missing for ${e[1]}`);
});

test('historical section is labelled and does not overlap current ids', () => {
  const hist = doc.slice(doc.indexOf('**Historical (2026-10-01'));
  assert.ok(hist.includes('not present in this worktree'));
  for (const e of entries) assert.ok(!hist.includes(e[1]));
});

test('doc records native window and cleanup markers', () => {
  assert.ok(doc.includes('NATIVE_WINDOW_PASS'));
  assert.ok(doc.includes('CLEANUP_PASS'));
});
