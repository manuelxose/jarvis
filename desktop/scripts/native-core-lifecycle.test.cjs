'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const L = require('./native-core-lifecycle.cjs')

const rec = (state, extra = {}) => '[jarvis-core] ' + JSON.stringify({ state, reason: 'x', attempt: 0, ...extra })

test('port parsing rejects protected, low, non-numeric and equal ports', () => {
  assert.deepEqual(L.parseArgs([]), { controlPort: 47921, eventsPort: 47922, evidence: null })
  assert.equal(L.parseArgs(['--control-port', '50000', '--events-port', '50001']).controlPort, 50000)
  for (const bad of ['47811', '47812', '80', '65536', '4x', '', '-1']) assert.throws(() => L.parseArgs(['--control-port', bad]), /invalid-port/, bad)
  assert.throws(() => L.parseArgs(['--control-port', '50000', '--events-port', '50000']), /control-equals-events/)
  assert.throws(() => L.parseArgs(['--bogus', '1']), /unknown-argument/)
  assert.throws(() => L.parseArgs(['--control-port']), /usage/)
})

test('derived config changes only whitelisted keys and records them', () => {
  const base = { daemon: { control_port: 47811, hotkey: 'x' }, memory: { db_path: 'memory/jarvis.db', other: 1 }, audio: { a: 1 } }
  const { config, changed } = L.deriveConfig(base, { controlPort: 50000, eventsPort: 50001, dbPath: 'C:\\t\\j.db' })
  assert.deepEqual(changed.map(c => c.key).sort(), [...L.DERIVED_KEYS].sort())
  assert.equal(base.daemon.control_port, 47811, 'input not mutated')
  const flat = (o, p = '') => Object.entries(o).flatMap(([k, v]) => (v && typeof v === 'object' ? flat(v, `${p}${k}.`) : [[`${p}${k}`, v]]))
  const diff = flat(config).filter(([k, v]) => JSON.stringify(Object.fromEntries(flat(base))[k]) !== JSON.stringify(v)).map(([k]) => k)
  assert.deepEqual(diff.sort(), [...L.DERIVED_KEYS].sort())
  assert.throws(() => L.deriveConfig({}, { dbPath: 'x' }), /malformed/)
  const noMemory = L.deriveConfig({ daemon: {} }, { controlPort: 50000, eventsPort: 50001, dbPath: 'C:\\t\\j.db' })
  assert.equal(noMemory.config.memory.db_path, 'C:\\t\\j.db')
  assert.throws(() => L.deriveConfig(base, { controlPort: 1, eventsPort: 2, dbPath: '' }), /db-path/)
})

test('venv lookup reports every checked path and never substitutes', () => {
  const root = path.resolve('/w/jarvis/')
  const none = L.locateVenv(root, () => false, {})
  assert.equal(none.found, null)
  assert.equal(none.checked.length, 4)
  assert.ok(none.checked.every(p => p.endsWith('python.exe')))
  const enclosing = L.locateVenv(root, p => p.includes(path.join('/w/jarvis', '.venv-win')), {})
  assert.ok(enclosing.found.startsWith(path.resolve('/w/jarvis')))
  const override = L.locateVenv(root, p => p === '/mnt/c/py/python.exe', { JARVIS_WIN_PYTHON: '/mnt/c/py/python.exe' })
  assert.equal(override.found, '/mnt/c/py/python.exe')
})

test('marker parsing ignores noise and partial lines; leak detection flags tokens', () => {
  const text = ['chromium noise', rec('connecting'), '[jarvis-core] {"state":', rec('connected'), rec('connected')].join('\r\n')
  const records = L.parseCoreMarkers(text)
  assert.deepEqual(L.stateSequence(records), ['connecting', 'connected'])
  assert.equal(L.markersLeakSecret(records), false)
  assert.equal(L.markersLeakSecret(L.parseCoreMarkers(rec('connected', { token: 'a' }))), true)
  assert.equal(L.markersLeakSecret(L.parseCoreMarkers(rec('connected', { reason: 'A'.repeat(43) }))), true)
})

test('sequence evaluation is ordered: attach, loss, then re-attach', () => {
  const seq = states => L.parseCoreMarkers(states.map(s => rec(s)).join('\n'))
  assert.equal(L.evaluateSequence(seq(['connecting']), 'attach'), false)
  assert.equal(L.evaluateSequence(seq(['connecting', 'connected']), 'attach'), true)
  assert.equal(L.evaluateSequence(seq(['connecting', 'disconnected']), 'loss'), false, 'loss before ever connecting is not a restart')
  assert.equal(L.evaluateSequence(seq(['connected', 'reconnecting']), 'loss'), true)
  assert.equal(L.evaluateSequence(seq(['connected', 'reconnecting']), 'reattach'), false)
  assert.equal(L.evaluateSequence(seq(['connected', 'reconnecting', 'disconnected', 'connected']), 'reattach'), true)
  assert.equal(L.evaluateSequence(seq(['connected', 'connected']), 'reattach'), false)
})

test('verdict aggregation: FAIL beats BLOCKED beats PASS and PASS needs every scenario', () => {
  const all = status => L.SCENARIOS.map(id => L.scenarioResult(id, status))
  assert.equal(L.aggregate(all('PASS')), 'PASS')
  assert.equal(L.aggregate(all('PASS').slice(1)), 'FAIL', 'missing scenario is not PASS')
  const mixed = all('PASS'); mixed[2] = L.scenarioResult(mixed[2].id, 'BLOCKED')
  assert.equal(L.aggregate(mixed), 'BLOCKED')
  mixed[3] = L.scenarioResult(mixed[3].id, 'FAIL')
  assert.equal(L.aggregate(mixed), 'FAIL')
  assert.throws(() => L.scenarioResult('x', 'MAYBE'), /invalid-status/)
  assert.deepEqual([L.exitCodeFor('PASS'), L.exitCodeFor('BLOCKED'), L.exitCodeFor('FAIL')], [0, 0, 1])
  assert.equal(L.exitCodeFor('BLOCKED', true), 3)
})

test('BLOCKED report names reason and checked paths for every scenario', () => {
  const report = L.blockedReport('windows-venv-missing', { checkedPaths: ['a', 'b'] })
  assert.equal(report.status, 'BLOCKED')
  assert.deepEqual(report.checkedPaths, ['a', 'b'])
  assert.equal(report.scenarios.length, L.SCENARIOS.length)
  assert.ok(report.scenarios.every(s => s.status === 'BLOCKED' && s.reason === 'windows-venv-missing'))
})

test('waitFor is bounded and returns null on timeout', () => {
  let t = 0, calls = 0
  assert.equal(L.waitFor(() => { calls++; return false }, 1000, { now: () => t, sleep: ms => { t += ms }, intervalMs: 400 }), null)
  assert.ok(calls >= 3 && calls <= 4)
  assert.equal(L.waitFor(() => 'ok', 1000, { now: () => t, sleep: () => {} }), 'ok')
})

test('runLifecycle refuses to start when a protected or chosen port has a listener', () => {
  const calls = []
  const ctx = { ps: script => { calls.push(script); return script.includes('Get-NetTCPConnection') ? [{ port: 47811, pid: 5 }] : (() => { throw new Error('unexpected spawn') })() }, ports: [47811], dirs: {}, readText: () => '' }
  const report = L.runLifecycle(ctx)
  assert.equal(report.status, 'BLOCKED')
  assert.ok(report.scenarios.every(s => s.status === 'BLOCKED' && /port-owned-by-non-harness-process:47811/.test(s.reason)))
  assert.equal(calls.filter(c => c.includes('Invoke-CimMethod')).length, 0)
})

test('script builders never interpolate arguments and the harness never kills by image name', () => {
  const evil = "x'; Remove-Item -Recurse C:\\ #"
  const script = L.step.spawnDetached({ exe: evil, args: [], cwd: '', stdout: '', stderr: '', env: [] })
  assert.ok(script.includes('spawn-unsafe-value'), 'detached spawn rejects cmd metacharacters')
  assert.ok(script.includes("ConvertFrom-Json '"))
  assert.ok(script.includes("x''; Remove-Item"), 'single quote doubled inside the PowerShell literal')
  assert.ok(!script.includes("x'; Remove-Item"), 'no unescaped quote breaks out of the literal')
  const source = require('node:fs').readFileSync(path.join(__dirname, 'native-core-lifecycle.cjs'), 'utf8')
  assert.ok(!/Stop-Process\s+-Name|taskkill|Get-Process\s+-Name|\/IM\b/i.test(source))
})
