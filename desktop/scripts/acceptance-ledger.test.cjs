'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const DOCS = path.resolve(__dirname, '../../docs')
const ledger = JSON.parse(fs.readFileSync(path.join(DOCS, 'phase-3-acceptance.json'), 'utf8'))
const STATUSES = ['PASS', 'FAIL', 'BLOCKED']
const entries = [...ledger.criteria, ...ledger.scenarios]
const cache = new Map()
const load = file => {
  if (!cache.has(file)) cache.set(file, JSON.parse(fs.readFileSync(path.join(DOCS, '..', file), 'utf8')))
  return cache.get(file)
}

function resolve(ref) {
  const [file, fragment] = ref.split('#')
  assert.ok(file.startsWith('docs/') && !file.includes('..'), `evidence must be a tracked docs/ path: ${ref}`)
  assert.ok(fs.existsSync(path.join(DOCS, '..', file)), `missing evidence file ${file}`)
  if (!fragment) return null
  const hit = (load(file).scenarios || []).find(s => s.id === fragment)
  assert.ok(hit, `fragment ${ref} does not resolve`)
  return hit
}

test('criteria and scenarios are non-empty and ids are unique', () => {
  assert.ok(ledger.criteria.length > 0 && ledger.scenarios.length > 0)
  assert.ok(ledger.briefSource && ledger.generatedAt)
  const ids = entries.map(e => e.id)
  assert.equal(new Set(ids).size, ids.length)
})

test('statuses are valid and non-PASS entries carry a reason', () => {
  for (const e of entries) {
    assert.ok(STATUSES.includes(e.status), `${e.id} status ${e.status}`)
    assert.ok(e.title && e.source && Array.isArray(e.evidence) && e.evidence.length > 0, `${e.id} shape`)
    if (e.status !== 'PASS') assert.ok(typeof e.reason === 'string' && e.reason.trim(), `${e.id} needs a reason`)
  }
})

test('every evidence file exists and every fragment resolves', () => {
  for (const e of entries) for (const ref of e.evidence) resolve(ref)
})

test('a PASS never cites a FAIL or BLOCKED source scenario', () => {
  for (const e of entries.filter(x => x.status === 'PASS')) {
    for (const ref of e.evidence) {
      const hit = resolve(ref)
      if (hit) assert.equal(hit.status, 'PASS', `${e.id} PASS cites ${ref} which is ${hit.status}`)
    }
  }
})

test('a FAIL or BLOCKED entry is not hidden behind all-PASS sources', () => {
  for (const e of entries.filter(x => x.status !== 'PASS')) {
    const fragments = e.evidence.map(resolve).filter(Boolean)
    if (fragments.length) assert.ok(fragments.some(h => h.status !== 'PASS') || e.status === 'BLOCKED', `${e.id} unexplained non-PASS`)
  }
})
