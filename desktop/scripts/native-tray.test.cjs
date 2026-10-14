'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const H = require('./native-tray.cjs')
const P = require('../src/tray-probe')
const { parseSmoke } = require('../src/main')
function fixture(t) {
  const root = fs.mkdtempSync(path.resolve('tray-test-'))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  return root
}
test('evidence argument parsing confines output and rejects malformed flags and symlinks', t => {
  const root = fixture(t)
  assert.deepEqual(H.parseArgs([], root), { evidence: null })
  assert.equal(H.parseArgs(['--evidence', 'result.json'], root).evidence, path.join(root, 'result.json'))
  for (const args of [['--bogus', 'x'], ['--evidence'], ['--evidence', ''], ['--evidence', 'x', 'extra']]) assert.throws(() => H.parseArgs(args, root), /usage/)
  assert.throws(() => H.parseArgs(['--evidence', '..'], root), /outside/)
  fs.symlinkSync(path.dirname(root), path.join(root, 'escape'))
  assert.throws(() => H.parseArgs(['--evidence', 'escape/result.json'], root), /escape/)
})
test('aggregate requires exactly one of every scenario; FAIL beats BLOCKED', () => {
  const rows = H.SCENARIOS.map(id => ({ id, status: 'PASS' }))
  assert.equal(H.aggregate(rows), 'PASS')
  assert.equal(H.aggregate(rows.slice(1)), 'FAIL')
  assert.equal(H.aggregate([...rows, rows[0]]), 'FAIL')
  rows[0].status = 'BLOCKED'; assert.equal(H.aggregate(rows), 'BLOCKED')
  rows[1].status = 'FAIL'; assert.equal(H.aggregate(rows), 'FAIL')
  rows[1].status = 'MAYBE'; assert.equal(H.aggregate(rows), 'FAIL')
})
test('restore plan retains exact absent/present values, unexpanded strings, binary and 64-bit integers', () => {
  for (const key of [H.RUN, H.APPROVED]) {
    const base = { key, name: 'jarvis-desktop', exists: false }
    assert.deepEqual(H.registryRestorePlan(base), { key, name: base.name, operation: 'delete' })
    for (const [kind, value] of [['String', ''], ['String', '"old app" --anything'], ['ExpandString', '%LOCALAPPDATA%\\app.exe'], ['Binary', [0, 255]], ['Binary', []], ['MultiString', ['a', '']], ['None', [3]], ['DWord', -1], ['QWord', '9223372036854775807'], ['QWord', '-9223372036854775808']]) {
      const snapshot = { ...base, exists: true, kind, value }
      const plan = H.registryRestorePlan(snapshot)
      assert.deepEqual(plan, { key, name: base.name, operation: 'set', kind, value })
      if (Array.isArray(plan.value)) { plan.value.push(2); assert.notDeepEqual(plan.value, snapshot.value) }
    }
  }
  for (const snapshot of [null, {}, { key: 'wrong', name: 'jarvis-desktop', exists: false }, { key: H.RUN, name: 'unrelated', exists: false }, { key: H.RUN, name: 'jarvis-desktop', exists: 'false' }]) assert.throws(() => H.registryRestorePlan(snapshot), /snapshot/)
  for (const [kind, value] of [['String', 1], ['Binary', [256]], ['Binary', 'x'], ['MultiString', [1]], ['QWord', 1], ['QWord', '9223372036854775808'], ['DWord', 1.2], ['Bogus', 'x']]) assert.throws(() => H.registryRestorePlan({ key: H.RUN, name: 'jarvis-desktop', exists: true, kind, value }), /kind/)
})
test('marker parsing tolerates partial/noisy/malformed logs without accepting arbitrary objects', () => {
  assert.deepEqual(H.markers('noise\n[jarvis-bootstrap] {\n[jarvis-bootstrap] null\n[jarvis-bootstrap] {}\n[jarvis-bootstrap] {"stage":"tray-ready","pid":71}\r\n'), [{ stage: 'tray-ready', pid: 71 }])
})
test('tray CLI validates fresh isolated state, login opt-in, exclusivity, duplicates and symlink escapes', t => {
  const root = fixture(t), state = path.join(root, 'state')
  const arg = `--tray-probe-state=${state}`
  assert.equal(P.parse([], parseSmoke, root), null)
  for (const args of [[arg, arg], [arg, '--tray-probe-bogus'], ['--tray-probe-login-item'], [arg, '--smoke-entry=overlay'], [arg, '--overlay-probe-state=x'], [arg, '--command-center-probe-state=x'], [arg, '--window-state-probe-state=x']]) assert.throws(() => P.parse(args, parseSmoke, root), /arguments/)
  assert.throws(() => P.parse(['--tray-probe-state=relative'], parseSmoke, root), /arguments/)
  assert.throws(() => P.parse([`--tray-probe-state=${path.dirname(root)}`], parseSmoke, root), /arguments/)
  fs.symlinkSync(path.dirname(root), path.join(root, 'link'))
  assert.throws(() => P.parse([`--tray-probe-state=${root}/link/new-state`], parseSmoke, root), /escape/)
  assert.deepEqual(P.parse([arg], parseSmoke, root), { state, loginItem: false })
  assert.throws(() => P.parse([arg], parseSmoke, root), /exists/)
  const opted = path.join(root, 'opted')
  assert.deepEqual(P.parse([`--tray-probe-state=${opted}`, '--tray-probe-login-item'], parseSmoke, root), { state: opted, loginItem: true })
})
function controller(request, loginItem = false, ioOptions = {}) {
  const jobs = [], quits = [], marks = [], writes = []
  const io = { existsSync: () => request !== undefined, lstatSync: () => ({ isSymbolicLink: () => false }), statSync: () => ({ size: 100 }), readFileSync: () => JSON.stringify(request), ...ioOptions }
  P.create({ state: '.', loginItem, later: (fn, ms) => jobs.push({ fn, ms }), quit: c => quits.push(c), mark: (stage, detail) => marks.push({ stage, ...detail }), setLaunchAtLogin: value => { writes.push(value); return value }, io })
  return { jobs, quits, marks, writes, poll: () => jobs.find(j => j.ms === 100).fn() }
}
test('controller performs login roundtrip only with CLI consent, deduplicates IDs and exits without Core access', () => {
  let r = { id: 1, operation: 'login-enable' }
  const c = controller(r, true, { readFileSync: () => JSON.stringify(r) })
  c.poll(); c.poll(); assert.deepEqual(c.writes, [true])
  r = { id: 2, operation: 'login-disable' }; c.poll(); assert.deepEqual(c.writes, [true, false])
  r = { id: 3, operation: 'exit-desktop' }; c.poll(); assert.deepEqual(c.quits, [undefined])
  assert.equal(c.marks.filter(m => m.stage === 'tray-probe-command').length, 3)
  const denied = controller({ id: 1, operation: 'login-enable' }); denied.poll()
  assert.deepEqual(denied.writes, []); assert.deepEqual(denied.quits, [1])
  const exit = controller({ id: 1, operation: 'exit-desktop' }); exit.poll(); assert.deepEqual(exit.quits, [undefined])
})
test('controller rejects malformed requests, unknown/Core operations, ordering, oversize, symlinks and IO errors; deadline bounded', () => {
  for (const r of [null, {}, [], { id: 0, operation: 'exit-desktop' }, { id: 9, operation: 'exit-desktop' }, { id: 1.5, operation: 'exit-desktop' }, { id: 2, operation: 'exit-desktop' }, { id: 1, operation: 'stop-core' }, { id: 1, operation: 'eval' }, { id: 1, operation: 'exit-desktop', extra: true }]) {
    const c = controller(r, true); c.poll(); assert.deepEqual(c.quits, [1]); assert.deepEqual(c.writes, [])
  }
  for (const io of [{ statSync: () => ({ size: 1025 }) }, { lstatSync: () => ({ isSymbolicLink: () => true }) }, { readFileSync: () => '{' }, { readFileSync: () => { throw new Error('disk') } }]) {
    const c = controller({ id: 1, operation: 'login-enable' }, true, io); c.poll(); assert.deepEqual(c.quits, [1]); assert.deepEqual(c.writes, [])
  }
  const idle = controller(undefined)
  idle.jobs.find(j => j.ms === 120000).fn(); assert.deepEqual(idle.quits, [1]); assert.equal(idle.marks[0].reason, 'deadline')
})
test('PowerShell builders preserve quoting, ownership and do not turn off sandboxing', () => {
  const script = H.spawnScript({ exe: "C:\\it's\\electron.exe", app: "C:\\app space", directory: 'C:\\state', login: false })
  assert.match(script, /it''s/); assert.doesNotMatch(script, /--tray-probe-login-item|no-sandbox/)
  assert.match(H.inspectScript('C:\\electron.exe', 'C:\\owned.json'), /Same-Process/)
  const helper = H.helperSpawnScript({ app: "C:\\it's\\app space", directory: 'C:\\state' })
  assert.match(helper, /Start-Process/)
  assert.match(helper, /it''s/)
  assert.match(helper, /ticks="\$\(\$p.StartTime.Ticks\)"/)
  assert.match(helper, /-EncodedCommand/)
  assert.doesNotMatch(helper, /step\.spawn|no-sandbox/)
  assert.throws(() => H.helperSpawnScript({ app: 'C:\\app\n', directory: 'C:\\state' }), /invalid-path/)
  assert.match(H.helperStop({ pid: 7, ticks: '3', path: 'C:\\powershell.exe' }), /StartTime.Ticks/)
  assert.match(H.registryRead(H.RUN), /reg\.exe query/)
  assert.match(H.hotkeyScript('C:\\state'), /UnregisterHotKey/)
  assert.doesNotMatch(require('node:fs').readFileSync(__filename.replace('.test.cjs', '.cjs'), 'utf8'), /Stop-Process\s+-Name|taskkill|no-sandbox/)
})
