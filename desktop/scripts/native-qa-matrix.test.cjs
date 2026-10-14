'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { SCENARIOS, parseArgs, aggregate, classifyTopology, mapHarness, scoreScenarios } = require('./native-qa-matrix.cjs')

const mon = (primary, dpi) => ({ primary, dpiX: dpi })
const dual = { monitors: [mon(true, 96), mon(false, 96)] }
const ok = (rows) => ({ exitCode: 0, evidence: { status: 'PASS', ...(rows ? { scenarios: rows } : {}) } })
const allOk = () => ({ smoke: ok(), overlay: ok(), 'command-center': ok([{ id: 'fullscreen-enter-exit', status: 'PASS' }]), 'window-state': ok(), 'core-lifecycle': ok(), tray: ok() })
const score = (o = {}) => scoreScenarios({ topology: dual, results: allOk(), orphans: [], ...o }).scenarios
const by = (rows, id) => rows.find(r => r.id === id)

test('parseArgs accepts none or a worktree-local evidence path only', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'qa-'))
  assert.deepEqual(parseArgs([], root), { evidence: null })
  assert.equal(parseArgs(['--evidence', 'out/a.json'], root).evidence, path.join(root, 'out/a.json'))
  assert.throws(() => parseArgs(['--evidence', '../x.json'], root), /evidence-outside-worktree/)
  assert.throws(() => parseArgs(['--evidence', '/etc/x.json'], root), /evidence-outside-worktree/)
  assert.throws(() => parseArgs(['--evidence'], root), /usage/)
  assert.throws(() => parseArgs(['--bogus', 'a'], root), /usage/)
  assert.throws(() => parseArgs(['--evidence', ''], root), /usage/)
})

test('mapHarness never PASSes without parsed evidence', () => {
  assert.equal(mapHarness('tray', { exitCode: 1, evidence: null }).status, 'FAIL')
  assert.equal(mapHarness('tray', { exitCode: 0, evidence: null }).status, 'FAIL')
  assert.equal(mapHarness('tray', undefined).status, 'FAIL')
  assert.equal(mapHarness('tray', { exitCode: 0, evidence: { status: 'MAYBE' } }).status, 'FAIL')
  assert.equal(mapHarness('tray', { exitCode: 0, evidence: {} }).status, 'FAIL')
})

test('mapHarness maps PASS, FAIL, BLOCKED and gaps truthfully', () => {
  assert.equal(mapHarness('smoke', ok()).status, 'PASS')
  assert.equal(mapHarness('smoke', { exitCode: 1, evidence: { status: 'FAIL', reason: 'boom' } }).reason, 'boom')
  const b = mapHarness('tray', { exitCode: 1, evidence: { status: 'BLOCKED', reason: 'missing-prereq' } })
  assert.deepEqual([b.status, b.reason], ['BLOCKED', 'missing-prereq'])
  const g = mapHarness('overlay', { exitCode: 0, evidence: { status: 'PASS_WITH_GAPS', acceptedGaps: [{ id: 'vscode' }] } })
  assert.equal(g.status, 'BLOCKED'); assert.match(g.reason, /vscode/)
})

test('mapHarness rejects PASS with nonzero exit or non-PASS rows', () => {
  assert.equal(mapHarness('smoke', { exitCode: 1, evidence: { status: 'PASS' } }).status, 'FAIL')
  assert.equal(mapHarness('tray', ok([{ id: 'a', status: 'PASS' }, { id: 'b', status: 'BLOCKED' }])).status, 'FAIL')
})

test('aggregate follows native-tray rules', () => {
  const full = st => SCENARIOS.map(id => ({ id, status: st }))
  assert.equal(aggregate(full('PASS')), 'PASS')
  assert.equal(aggregate([...full('PASS').slice(1), { id: SCENARIOS[0], status: 'BLOCKED' }]), 'BLOCKED')
  assert.equal(aggregate([...full('BLOCKED').slice(1), { id: SCENARIOS[0], status: 'FAIL' }]), 'FAIL')
  assert.equal(aggregate(full('PASS').slice(1)), 'FAIL')
  assert.equal(aggregate([...full('PASS'), { id: SCENARIOS[0], status: 'PASS' }]), 'FAIL')
  assert.equal(aggregate([...full('PASS').slice(1), { id: 'unknown', status: 'PASS' }]), 'FAIL')
  assert.equal(aggregate([...full('PASS').slice(1), { id: SCENARIOS[0], status: 'WEIRD' }]), 'FAIL')
  assert.equal(aggregate([]), 'FAIL')
})

test('classifyTopology records count, primary and mixed DPI', () => {
  assert.equal(classifyTopology({ monitors: [mon(true, 96)] }).multiMonitor, false)
  const mixed = classifyTopology({ monitors: [mon(true, 96), mon(false, 144)] })
  assert.deepEqual([mixed.multiMonitor, mixed.mixedDpi, mixed.scales], [true, true, [100, 150]])
  assert.equal(classifyTopology(dual).mixedDpi, false)
  assert.equal(classifyTopology({ monitors: [mon(true, null), mon(false, 96)] }).dpiKnown, false)
  assert.equal(classifyTopology(null).count, 0)
})

test('all-green inputs score every scenario PASS', () => {
  const rows = score()
  assert.deepEqual(rows.map(r => r.id), SCENARIOS)
  assert.ok(rows.every(r => r.status === 'PASS'))
  assert.equal(aggregate(rows), 'PASS')
})

test('single monitor blocks multi-monitor and DPI scenarios but keeps others', () => {
  const rows = score({ topology: { monitors: [mon(true, 96)] } })
  assert.equal(by(rows, 'window-state-restore-multi-monitor').status, 'BLOCKED')
  assert.equal(by(rows, 'dpi-scaling-topology').status, 'BLOCKED')
  assert.equal(by(rows, 'shell-launch').status, 'PASS')
  assert.equal(aggregate(rows), 'BLOCKED')
})

test('harness failure wins over topology block; missing topology fails', () => {
  const results = { ...allOk(), 'window-state': { exitCode: 1, evidence: { status: 'FAIL', reason: 'x' } } }
  assert.equal(by(score({ topology: { monitors: [mon(true, 96)] }, results }), 'window-state-restore-multi-monitor').status, 'FAIL')
  const rows = score({ topology: null, topologyError: 'bridge-error' })
  assert.equal(by(rows, 'dpi-scaling-topology').status, 'FAIL')
  assert.equal(aggregate(rows), 'FAIL')
})

test('fullscreen requires its row; orphans and scan errors fail', () => {
  const results = { ...allOk(), 'command-center': ok([{ id: 'windowed', status: 'PASS' }]) }
  assert.equal(by(score({ results }), 'fullscreen-escape').status, 'FAIL')
  assert.equal(by(score({ orphans: [{ pid: 4, name: 'electron.exe' }] }), 'no-orphan-processes').status, 'FAIL')
  assert.equal(by(score({ orphans: undefined, orphanError: 'x' }), 'no-orphan-processes').status, 'FAIL')
})

test('missing harness evidence fails its scenario', () => {
  const results = { ...allOk(), tray: { exitCode: 1, evidence: null } }
  const rows = score({ results })
  assert.equal(by(rows, 'tray-shortcuts-login').status, 'FAIL')
  assert.equal(aggregate(rows), 'FAIL')
})
