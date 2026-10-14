'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

// Only tracked evidence is consumed; native PNG/run directories are not test
// dependencies. This validates truthfulness of the ledger, not visual parity.
const evidence = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../../docs/desktop-visual-evidence.json'), 'utf8'))

test('tracked visual evidence has nonempty uniquely identified scenarios', () => {
  assert.ok(Array.isArray(evidence.scenarios) && evidence.scenarios.length > 0)
  const ids = evidence.scenarios.map(row => row.id)
  assert.ok(ids.every(id => typeof id === 'string' && id.trim()))
  assert.equal(new Set(ids).size, ids.length)
})

test('every visual verdict is valid and every non-PASS has a concrete reason', () => {
  for (const row of evidence.scenarios) {
    assert.ok(['PASS', 'FAIL', 'BLOCKED'].includes(row.status), row.id)
    if (row.status !== 'PASS') assert.ok(typeof row.reason === 'string' && row.reason.trim(), row.id)
  }
})

test('PASS requires a finite numeric ratio within the frozen 0.01 maximum', () => {
  assert.equal(evidence.perChannelTolerance, 51)
  assert.equal(evidence.maxDiffRatio, 0.01)
  for (const row of evidence.scenarios) {
    if (row.status === 'PASS') {
      assert.ok(Number.isFinite(row.ratio) && row.ratio >= 0 && row.ratio <= 0.01, row.id)
      assert.ok(typeof row.capture === 'string' && row.capture.trim(), row.id)
    }
  }
})

const crypto = require('node:crypto')
const root = path.resolve(__dirname, '../..')
const win32 = JSON.parse(fs.readFileSync(path.join(root, 'docs/desktop-visual-evidence-win32.json'), 'utf8'))
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'desktop/visual-baselines/manifest.json'), 'utf8'))

test('win32 baseline evidence follows the same verdict rules and names its reference set', () => {
  assert.equal(win32.referenceSet, 'win32-baseline')
  assert.equal(win32.perChannelTolerance, 51)
  assert.equal(win32.maxDiffRatio, 0.01)
  const ids = win32.scenarios.map(row => row.id)
  assert.equal(new Set(ids).size, ids.length)
  for (const row of win32.scenarios) {
    assert.ok(['PASS', 'FAIL', 'BLOCKED'].includes(row.status), row.id)
    if (row.status !== 'PASS') assert.ok(typeof row.reason === 'string' && row.reason.trim(), row.id)
    else assert.ok(Number.isFinite(row.ratio) && row.ratio >= 0 && row.ratio <= 0.01 && row.capture, row.id)
  }
})

test('every win32 PASS row references a tracked baseline whose sha256 matches the manifest', () => {
  const passes = win32.scenarios.filter(row => row.status === 'PASS')
  assert.ok(passes.length > 0)
  for (const row of passes) {
    assert.ok(row.reference.startsWith('desktop/visual-baselines/'), row.id)
    const entry = manifest.files.find(f => f.file === row.reference && f.id === row.id)
    assert.ok(entry, row.id)
    assert.equal(crypto.createHash('sha256').update(fs.readFileSync(path.join(root, row.reference))).digest('hex'), entry.sha256, row.id)
    assert.equal(win32.baselines.find(b => b.id === row.id).sha256, entry.sha256, row.id)
  }
})
