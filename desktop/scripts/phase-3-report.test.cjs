'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const docs = path.resolve(__dirname, '..', '..', 'docs')
const read = rel => fs.readFileSync(path.join(docs, rel), 'utf8')
const ledger = JSON.parse(read('phase-3-acceptance.json'))
const entries = [...ledger.criteria, ...ledger.scenarios]

function rowsFor(report, id) {
  return report.split('\n')
    .filter(line => line.startsWith('|'))
    .map(line => line.split('|').slice(1, -1).map(cell => cell.trim()))
    .filter(cells => cells[0] === id)
}

test('every ledger id has report rows, all with its exact status; verdict rows link evidence', () => {
  const report = read('phase-3-report.md')
  assert.ok(entries.length > 0)
  for (const e of entries) {
    const rows = rowsFor(report, e.id)
    assert.ok(rows.length >= 1, `${e.id} rows`)
    for (const cells of rows) assert.equal(cells[1], e.status, `${e.id} status`)
    assert.ok(rows.some(cells => /\]\(.+\)/.test(cells[cells.length - 1])), `${e.id} evidence link`)
  }
})

test('report never shows PASS for a FAIL or BLOCKED ledger entry', () => {
  const report = read('phase-3-report.md')
  for (const e of entries.filter(x => x.status !== 'PASS')) {
    for (const cells of rowsFor(report, e.id)) assert.notEqual(cells[1], 'PASS', e.id)
  }
})

test('report does not contain rows for ids absent from the ledger', () => {
  const report = read('phase-3-report.md')
  const known = new Set(entries.map(e => e.id))
  for (const line of report.split('\n').filter(l => /^\|\s*[CS]\d{2}\s*\|/.test(l))) {
    assert.ok(known.has(line.split('|')[1].trim()), line)
  }
})

test('handoff documents every desktop package.json script', () => {
  const handoff = read('desktop-handoff.md')
  const pkg = JSON.parse(fs.readFileSync(path.resolve(__dirname, '..', 'package.json'), 'utf8'))
  for (const name of Object.keys(pkg.scripts)) {
    const cmd = name === 'start' || name === 'test' ? `npm --prefix desktop ${name}` : `npm --prefix desktop run ${name}`
    assert.ok(handoff.includes(cmd), name)
  }
})
