'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { spawnSync } = require('node:child_process')
const probe = require('./native-overlay.cjs')
const smoke = require('./native-smoke.cjs')
const { createProbe, parseProbe, GEOMETRY } = require('../src/overlay-probe')
const { parseSmoke } = require('../src/main')
const target = { pid: 123, started: '638000000000000001', path: 'C:\\fixture\\app.exe' }
const config = { exe: 'C:\\fixture\\electron.exe', app: 'C:\\fixture\\desktop', root: 'C:\\fixture', directory: 'C:\\fixture\\desktop-native-overlay-fixture' }
function fixture(t) { const dir = fs.mkdtempSync(path.resolve('native-overlay-fixture-')); t.after(() => fs.rmSync(dir, { recursive: true, force: true })); return dir }
function input() { return { target, revalidated: { ...target }, hwnd: '12345', foreground: '12345', visible: true, hitValidated: true, clientBounds: { x: 0, y: 0, width: 800, height: 600 }, client: { x: 20, y: 20 }, screen: { x: -100, y: 120 }, flags: [2] } }
function good() {
  return { platform: 'win32', realInput: true, preflight: { ctypes: true }, cleanup: { remaining: [], errors: [] }, scenarios: probe.REQUIRED.map(id => ({ id, status: 'PASS', target, hwnd: '12345', inputs: [input(), input()], screenshots: ['screenshot-01.bmp', 'screenshot-02.bmp'], receipt: { kind: 'controlled-page-pointer', before: 0, after: 1, correlated: true }, observation: { rendererNativeAgree: true, roundtrip: true, dx: 24, dy: 12, borderless: true, topmost: true, aboveRecipient: true, marginEqual: true, cardDifferent: true } })) }
}
function run(t, bridge, report = good()) { return probe.run({ config, directory: fixture(t), bridge, readReport: () => report, exists: () => true }) }
const host = () => ({ error: null, electronExit: 0, pythonExit: 0, remaining: [], cleanupErrors: [] })
const recovery = () => ({ remaining: [], cleanupErrors: [] })
test('import has no CLI launch or stdout side effect', () => {
  const result = spawnSync(process.execPath, ['-e', `require(${JSON.stringify(require('node:path').join(__dirname, 'native-overlay.cjs'))});require(${JSON.stringify(require('node:path').join(__dirname, '..', 'src', 'overlay-probe.js'))})`], { encoding: 'utf8', timeout: 2000 })
  assert.equal(result.status, 0); assert.equal(result.stdout, ''); assert.equal(result.stderr, '')
})
test('complete contract evidence aggregates PASS (inline fixtures, NOT Windows acceptance)', () => { assert.equal(probe.validate(good(), () => true), 'PASS') })
for (const [name, mutate, expected] of [
  ['non-Windows mock', r => { r.platform = 'linux' }, /incomplete-native/],
  ['simulated input', r => { r.realInput = false }, /incomplete-native/],
  ['missing ctypes', r => { r.preflight = null }, /incomplete-native/],
  ['missing app scenario', r => { r.scenarios.pop() }, /incomplete-native/],
  ['duplicate scenario', r => { r.scenarios[1] = r.scenarios[0] }, /incomplete-native/],
  ['invented scenario', r => { r.scenarios[1].id = 'unknown' }, /missing-or-invalid/],
  ['invalid status', r => { r.scenarios[0].status = 'OK' }, /missing-or-invalid/],
  ['cleanup survivor', r => { r.cleanup.remaining = [target] }, /cleanup-failed/],
  ['cleanup access denied', r => { r.cleanup.errors = ['access-denied'] }, /cleanup-failed/],
  ['unobserved cleanup', r => { delete r.cleanup.errors }, /cleanup-failed/],
  ['absent screenshot', r => { r.scenarios[0].screenshots = [] }, /incomplete-pass/],
  ['missing identity', r => { r.scenarios[0].target = null }, /incomplete-pass/],
  ['invalid coordinates', r => { r.scenarios[0].inputs[0].client.x = NaN }, /malformed-coordinates/],
  ['off-target edge', r => { r.scenarios[0].inputs[0].client.x = 800 }, /malformed-coordinates/],
  ['off-target negative', r => { r.scenarios[0].inputs[0].client.y = -1 }, /malformed-coordinates/],
  ['focus theft', r => { r.scenarios[0].inputs[0].foreground = '99' }, /identity-or-focus/],
  ['PID reuse', r => { r.scenarios[0].inputs[0].revalidated.started = '638000000000000099' }, /identity-or-focus/],
  ['HWND zero', r => { r.scenarios[0].inputs[0].hwnd = '0' }, /identity-or-focus/],
  ['hidden target', r => { r.scenarios[0].inputs[0].visible = false }, /identity-or-focus/],
  ['foreign hit', r => { r.scenarios[0].inputs[0].hitValidated = false }, /identity-or-focus/],
  ['scenario identity swap', r => { r.scenarios[0].target = { ...target, pid: 999 } }, /identity-mismatch/],
  ['scenario HWND swap', r => { r.scenarios[0].hwnd = '67890' }, /identity-mismatch/],
  ['unobserved receipt', r => { r.scenarios[0].receipt = null }, /independent-recipient/],
  ['renderer Boolean receipt', r => { r.scenarios[0].receipt.kind = 'renderer-boolean' }, /independent-recipient/],
  ['WindowFromPoint receipt', r => { r.scenarios[0].receipt.kind = 'WindowFromPoint' }, /independent-recipient/],
  ['uncorrelated receipt', r => { r.scenarios[0].receipt.correlated = false }, /independent-recipient/],
  ['unchanged receipt', r => { r.scenarios[0].receipt.after = 0 }, /independent-recipient/],
  ['move-event-only receipt', r => { r.scenarios[0].inputs = [] }, /independent-recipient/],
  ['mode disagreement', r => { r.scenarios.find(s => s.id === 'overlay.control').observation.rendererNativeAgree = false }, /mode-response/],
  ['drag without native movement', r => { Object.assign(r.scenarios.find(s => s.id === 'overlay.drag').observation, { dx: 0, dy: 0 }) }, /native-displacement/],
  ['non-topmost', r => { r.scenarios.find(s => s.id === 'overlay.native-surface').observation.topmost = false }, /native-styles/],
  ['CSS-only alpha', r => { r.scenarios.find(s => s.id === 'overlay.compositor').screenshots = ['one'] }, /compositor-observation/],
]) test(name + ' cannot be promoted to PASS', () => { const report = good(); mutate(report); assert.throws(() => probe.validate(report, () => true), expected) })
test('missing screenshots on disk cannot pass', () => { assert.throws(() => probe.validate(good()), /incomplete-pass/) })
test('required missing app stays BLOCKED; failure dominates blocked; reason required', () => {
  const r = good(); r.scenarios[0] = { id: r.scenarios[0].id, status: 'BLOCKED', reason: 'application-missing' }
  assert.equal(probe.validate(r, () => true), 'BLOCKED')
  r.scenarios[1] = { id: r.scenarios[1].id, status: 'FAIL', reason: 'receipt-mismatch' }
  assert.equal(probe.validate(r, () => true), 'FAIL')
  delete r.scenarios[0].reason; assert.throws(() => probe.validate(r, () => true), /missing-gap-reason/)
})
test('successful contracts still perform independently observed recovery', t => {
  let calls = 0; const r = run(t, () => ++calls === 1 ? host() : recovery())
  assert.equal(calls, 2); assert.equal(r.status, 'PASS')
})
for (const failure of ['bridge-ETIMEDOUT', 'bridge-loss', 'access-denied', 'malformed-json']) test(failure + ' preserves failure after bounded recovery', t => {
  let calls = 0; const r = run(t, () => { if (++calls === 1) throw new Error(failure); return recovery() })
  assert.equal(calls, 2); assert.equal(r.status, 'FAIL'); assert.equal(r.reason, failure)
})
test('cleanup failure remains visible after successful native session', t => {
  let calls = 0; const r = run(t, () => ++calls === 1 ? host() : { remaining: [target], cleanupErrors: ['denied'] })
  assert.equal(r.status, 'FAIL'); assert.equal(r.cleanupFailure, 'cleanup-failed')
})
test('blocked native report requires nonzero Python exit and cannot become PASS', t => {
  const report = good(); report.scenarios[0] = { id: report.scenarios[0].id, status: 'BLOCKED', reason: 'app-missing' }
  let calls = 0
  assert.equal(run(t, () => ++calls === 1 ? { ...host(), pythonExit: 1 } : recovery(), report).status, 'BLOCKED')
  calls = 0
  assert.equal(run(t, () => ++calls === 1 ? host() : recovery(), report).reason, 'probe-exit-verdict-mismatch')
})
test('actual command seam bounds timeout, missing executable and malformed bridge response', () => {
  assert.throws(() => smoke.command('overlay-no-such-executable', [], 100), /ENOENT/)
  assert.throws(() => smoke.command(process.execPath, ['-e', 'setInterval(()=>{},1000)'], 100), /ETIMEDOUT/)
  assert.throws(() => JSON.parse(smoke.command(process.execPath, ['-e', 'console.log("nope")'], 1000)), SyntaxError)
})

function controller(t, io = fs) {
  const dir = fixture(t), state = path.join(dir, 'state'); fs.mkdirSync(state)
  const jobs = [], exits = [], modes = [], scripts = []
  const win = { isDestroyed: () => false, isVisible: () => true, getBounds: () => ({ x: 10, y: 20, width: 420, height: 160 }), getNativeWindowHandle: () => Buffer.from('3930000000000000', 'hex'), webContents: { getZoomFactor: () => 1, executeJavaScript: async s => { scripts.push(s); return { card: { x: 0, y: 0, width: 200, height: 100 } } } } }
  createProbe({ state, io, later: (fn, ms) => ms === 50 ? queueMicrotask(fn) : jobs.push({ fn, ms }), quit: code => exits.push(code || 0), getWindow: () => win, getMode: () => 'OVERLAY_COMPACT', requestMode: mode => { modes.push(mode); return true } })
  async function step(value) {
    fs.writeFileSync(path.join(state, 'request.json'), typeof value === 'string' ? value : JSON.stringify(value))
    const index = jobs.findIndex(j => j.ms === 100); assert.ok(index >= 0)
    await jobs.splice(index, 1)[0].fn()
  }
  return { state, jobs, exits, modes, scripts, win, step }
}
test('CLI probe reuses worktree-only fresh state parsing and rejects mixed/unknown args', t => {
  const root = fixture(t), state = path.join(root, 'state')
  assert.equal(parseProbe([], parseSmoke, root), null)
  for (const args of [['--overlay-probe-extra=x'], ['--overlay-probe-state=relative'], [`--overlay-probe-state=${state}`, '--smoke-entry=overlay']]) assert.throws(() => parseProbe(args, parseSmoke, root))
  assert.equal(parseProbe([`--overlay-probe-state=${state}`], parseSmoke, root), state)
  assert.throws(() => parseProbe([`--overlay-probe-state=${state}`], parseSmoke, root), /state-exists/)
})
test('fixed sequence handles duplicate IDs, observes geometry only, hides/shows/explicitly quits', async t => {
  const h = controller(t)
  await h.step({ id: 1, operation: 'observe' }); await h.step({ id: 1, operation: 'observe' })
  assert.equal(h.scripts.length, 1); assert.equal(h.scripts[0], GEOMETRY)
  assert.equal(JSON.parse(fs.readFileSync(path.join(h.state, 'response.json'))).hwnd, '12345')
  await h.step({ id: 2, operation: 'hide' }); await h.step({ id: 3, operation: 'observe' }); await h.step({ id: 4, operation: 'show' }); await h.step({ id: 5, operation: 'quit' })
  assert.deepEqual(h.modes, ['HIDDEN', 'OVERLAY_COMPACT']); assert.deepEqual(h.exits, [0])
  assert.equal(h.jobs.filter(j => j.ms === 100).length, 0)
})
for (const [name, value] of [['arbitrary eval', { id: 1, operation: 'eval', script: 'evil' }], ['shell', { id: 1, operation: 'shell' }], ['input', { id: 1, operation: 'send-input' }], ['coordinates', { id: 1, operation: 'observe', x: 1 }], ['out of order', { id: 2, operation: 'observe' }], ['over budget', { id: 201, operation: 'observe' }], ['negative ID', { id: -1, operation: 'observe' }], ['malformed JSON', '{'], ['invalid sequence', { id: 1, operation: 'show' }]]) test('main-only controller denies ' + name, async t => {
  const h = controller(t); await h.step(value); assert.deepEqual(h.exits, [1]); assert.equal(h.scripts.length, 0)
})
test('main-only controller bounds cycles and session; lost window and rejected observation exit nonzero', async t => {
  const h = controller(t)
  for (let i = 0; i < 4; i++) { await h.step({ id: i * 2 + 1, operation: 'hide' }); await h.step({ id: i * 2 + 2, operation: 'show' }) }
  await h.step({ id: 9, operation: 'hide' }); assert.deepEqual(h.exits, [1])
  const lost = controller(t); lost.win.isDestroyed = () => true; await lost.step({ id: 1, operation: 'observe' }); assert.deepEqual(lost.exits, [1])
  const reject = controller(t); reject.win.webContents.executeJavaScript = () => Promise.reject(new Error('lost renderer')); await reject.step({ id: 1, operation: 'observe' }); assert.deepEqual(reject.exits, [1])
  const hung = controller(t); hung.jobs.find(j => j.ms === 120000).fn(); assert.deepEqual(hung.exits, [1])
})

test('response replacement retries transient UNC denial without repeating observation', async t => {
  let calls = 0
  const h = controller(t, { ...fs, renameSync: (...args) => {
    if (++calls < 3) throw Object.assign(new Error('sharing violation'), { code: 'EPERM' })
    return fs.renameSync(...args)
  } })
  await h.step({ id: 1, operation: 'observe' })
  assert.equal(calls, 3); assert.equal(h.scripts.length, 1); assert.deepEqual(h.exits, [])
  assert.equal(JSON.parse(fs.readFileSync(path.join(h.state, 'response.json'))).id, 1)
})
for (const code of ['EACCES', 'EINVAL']) test('response replacement fails closed and bounded on ' + code, async t => {
  let calls = 0
  const h = controller(t, { ...fs, renameSync: () => {
    calls++; throw Object.assign(new Error('denied'), { code })
  } })
  fs.writeFileSync(path.join(h.state, 'response.json'), 'previous-valid-response')
  await h.step({ id: 1, operation: 'observe' })
  assert.equal(calls, code === 'EACCES' ? 6 : 1); assert.deepEqual(h.exits, [1])
  assert.equal(fs.readFileSync(path.join(h.state, 'response.json'), 'utf8'), 'previous-valid-response')
})

let hasBridge = false
try { hasBridge = smoke.command('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', 'Write-Output ready'], 10000) === 'ready' } catch {}
test('actual constrained PowerShell emergency recovery skips PID reuse and foreign images', { skip: !hasBridge }, () => {
  const records = [{ ...target, path: config.exe }, { ...target, pid: 124 }, { ...target, pid: 125 }]
  const doubles = `
$script:live=@{123=@{Id=123;Path=${smoke.psLiteral(config.exe)};StartTime=[datetime]::new(${target.started},[DateTimeKind]::Utc)};124=@{Id=124;Path=${smoke.psLiteral(target.path)};StartTime=[datetime]::new(638000000000000099,[DateTimeKind]::Utc)};125=@{Id=125;Path='C:\\foreign.exe';StartTime=[datetime]::new(${target.started},[DateTimeKind]::Utc)}}
function Get-CimInstance { return @() }
function Test-Path { return $true }
function Get-Content { return ${smoke.psLiteral(JSON.stringify(records))} }
function Get-Process { param($Id,$ErrorAction) return $script:live[[int]$Id] }
function Stop-Process { param($InputObject,[switch]$Force,$ErrorAction) $script:live.Remove([int]$InputObject.Id) }
`
  const result = smoke.powershell(doubles + probe.recoveryScript(config))
  assert.deepEqual(result.stopped, [123]); assert.deepEqual(result.remaining, []); assert.deepEqual(result.cleanupErrors, [])
})
test('Python stdlib negative guards and receipt server run without native launch', () => {
  const result = spawnSync('python3', ['-B', require('node:path').join(__dirname, 'windows-overlay-probe.test.py')], { encoding: 'utf8', timeout: 20000 })
  assert.equal(result.status, 0, result.stdout + result.stderr)
})
test('accept-gaps only turns BLOCKED third-party recipients into reported gaps', () => {
  const row = (id, status, reason) => ({ id, status, reason })
  const evidence = native => ({ status: 'BLOCKED', native: { scenarios: native } })
  const gap = [row('browser.empty-root', 'PASS'), row('vscode.empty-root', 'BLOCKED', 'observation-timeout')]
  const accepted = probe.acceptGaps(evidence(gap), ['vscode'])
  assert.equal(accepted.status, 'PASS_WITH_GAPS')
  assert.deepEqual(accepted.acceptedGaps, [{ id: 'vscode.empty-root', reason: 'observation-timeout' }])
  assert.equal(probe.acceptGaps(evidence(gap), []).status, 'BLOCKED')
  assert.equal(probe.acceptGaps(evidence(gap), ['browser']).status, 'BLOCKED')
  assert.equal(probe.acceptGaps(evidence([row('overlay.drag', 'BLOCKED', 'x')]), ['vscode']).status, 'BLOCKED')
  assert.equal(probe.acceptGaps(evidence([row('vscode.empty-root', 'FAIL', 'x')]), ['vscode']).status, 'BLOCKED')
})
