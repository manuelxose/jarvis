'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const h = require('./native-window-state.cjs')
const config = { exe: 'C:\\runtime\\electron.exe', app: 'C:\\work\\desktop', root: 'C:\\work', directory: 'C:\\work\\desktop-native-window-state-run-t\\a-1', state: 'C:\\work\\desktop-native-window-state-state-a', phase: 'restore', scenario: 'maximized-restore', launch: 2 }
const target = { pid: 123, started: '1234567890', path: config.exe }
const primary = { handle: '100', primary: true, monitor: { x: 0, y: 0, width: 1920, height: 1080 }, workArea: { x: 0, y: 0, width: 1920, height: 1040 } }
const second = { handle: '200', primary: false, monitor: { x: 1920, y: 0, width: 1920, height: 1080 }, workArea: { x: 1920, y: 0, width: 1920, height: 1080 } }
const obs = (phase, rect, monitor = primary, extra = {}) => ({ phase, host: 'command-center', hwnd: '42', rect, monitorHandle: monitor.handle, workArea: { ...monitor.workArea }, zoomed: false, mode: 'COMMAND_CENTER_WINDOWED', ...extra })
function report() {
  const common = { status: 'PASS', target: { ...target }, hwnd: '42', monitors: [primary, second] }
  const big = { x: 2020, y: 100, width: 1000, height: 700 }
  return { platform: 'win32', preflight: { ctypes: true }, cleanup: { remaining: [], errors: [] }, scenarios: [
    { ...common, id: 'persist-restore-secondary', seed: { kind: 'saved' }, observations: [obs('saved', big, second), obs('restored', { ...big, x: big.x + 1 }, second)] },
    { ...common, id: 'offscreen-recovery', seed: { kind: 'seeded', rect: { x: -30000, y: -30000, width: 1000, height: 700 } }, observations: [obs('restored', { x: 460, y: 170, width: 1000, height: 700 })] },
    { ...common, id: 'resolution-shrink-clamp', seed: { kind: 'seeded', rect: { x: 100, y: 100, width: 9000, height: 6000 } }, observations: [obs('restored', { x: 0, y: 0, width: 1920, height: 1040 })] },
    { ...common, id: 'overlay-position-restore', seed: { kind: 'seeded', rect: { x: 200, y: 200, width: 420, height: 160 } }, observations: [obs('restored', { x: 201, y: 200, width: 420, height: 160 }, primary, { host: 'overlay', mode: 'OVERLAY_COMPACT' }), obs('expanded', { x: 201, y: 200, width: 420, height: 520 }, primary, { host: 'overlay', mode: 'OVERLAY_EXPANDED' })] },
    { ...common, id: 'maximized-restore', seed: { kind: 'saved' }, observations: [obs('saved', { x: -8, y: -8, width: 1936, height: 1056 }, primary, { zoomed: true }), obs('restored', { x: -8, y: -8, width: 1936, height: 1056 }, primary, { zoomed: true })] },
    { id: 'physical-display-change', status: 'BLOCKED', reason: 'requires-owner-physical-display-change' },
  ] }
}
const scenario = (r, id) => r.scenarios.find(s => s.id === id)

test('import is inert and a well-formed report is PASS_WITH_GAPS only for the physical gap', () => {
  assert.equal(h.validate(report()), 'PASS_WITH_GAPS')
  const r = report(); const e = { before: [primary, second], after: [primary] }
  Object.assign(scenario(r, 'physical-display-change'), { status: 'PASS', ownerEvidence: { performedBy: 'owner', note: 'unplugged monitor 2', ...e } }); delete scenario(r, 'physical-display-change').reason
  assert.equal(h.validate(r), 'PASS')
})

test('missing scenario, duplicate id and bad envelope are rejected', () => {
  const r = report(); r.scenarios.pop(); assert.throws(() => h.validate(r), /incomplete/)
  const d = report(); d.scenarios[1].id = d.scenarios[0].id; assert.throws(() => h.validate(d), /incomplete/)
  for (const fn of [r => { r.platform = 'linux' }, r => { r.preflight.ctypes = false }, r => { r.cleanup.remaining = [{ pid: 1 }] }, r => { r.cleanup.errors = ['x'] }, r => { delete r.cleanup }]) {
    const x = report(); fn(x); assert.throws(() => h.validate(x))
  }
})

test('a PASS without Win32 evidence is rejected', () => {
  for (const id of h.REQUIRED.slice(0, 5)) for (const fn of [s => { delete s.hwnd }, s => { s.hwnd = '0' }, s => { s.observations = [] }, s => { delete s.monitors }, s => { s.target.pid = 0 },
    s => { delete s.observations[0].rect }, s => { s.observations[0].monitorHandle = 'nope' }, s => { s.observations[0].workArea.width = 1 }, s => { s.observations[0].rect.width = NaN }]) {
    const r = report(); fn(scenario(r, id)); assert.throws(() => h.validate(r), undefined, `${id}: ${fn}`)
  }
})

test('rects outside every monitor workArea are rejected', () => {
  for (const id of ['offscreen-recovery', 'resolution-shrink-clamp', 'persist-restore-secondary']) {
    const r = report(); const s = scenario(r, id); s.observations.find(o => o.phase === 'restored').rect = { x: 1900, y: 1000, width: 1000, height: 700 }
    if (id === 'persist-restore-secondary') s.observations[0].rect = { ...s.observations[1].rect }
    assert.throws(() => h.validate(r), /outside-workarea/)
  }
  const r = report(); scenario(r, 'maximized-restore').observations[1].rect.width = 2100; assert.throws(() => h.validate(r), /outside-workarea/)
})

test('secondary scenario must restore on the saved, non-primary monitor within 2px', () => {
  for (const fn of [s => { s.observations[1].monitorHandle = '100'; s.observations[1].workArea = { ...primary.workArea }; s.observations[1].rect = { x: 100, y: 100, width: 1000, height: 700 } },
    s => { s.observations[1].rect.x += 3 }, s => { s.observations[1].rect.height += 3 }, s => { s.observations = s.observations.slice(1) }]) {
    const r = report(); fn(scenario(r, 'persist-restore-secondary')); assert.throws(() => h.validate(r), /secondary|restored/)
  }
  const onPrimary = report(), s = scenario(onPrimary, 'persist-restore-secondary')
  for (const o of s.observations) { o.monitorHandle = '100'; o.workArea = { ...primary.workArea }; o.rect = { x: 100, y: 100, width: 1000, height: 700 } }
  assert.throws(() => h.validate(onPrimary), /secondary/)
})

test('single-monitor cannot PASS the secondary scenario and is BLOCKED with a reason', () => {
  const r = report(); scenario(r, 'persist-restore-secondary').monitors = [primary]
  for (const o of scenario(r, 'persist-restore-secondary').observations) { o.monitorHandle = '100'; o.workArea = { ...primary.workArea }; o.rect = { x: 100, y: 100, width: 1000, height: 700 } }
  assert.throws(() => h.validate(r), /single-monitor/)
  scenario(r, 'persist-restore-secondary').status = 'BLOCKED'
  assert.throws(() => h.validate(r), /gap-reason/)
  scenario(r, 'persist-restore-secondary').reason = 'single-monitor'
  assert.equal(h.validate(r), 'BLOCKED')
})

test('seeded scenarios prove their premise: off-screen seed, overflowing seed, overlay origin, maximized', () => {
  const cases = [
    ['offscreen-recovery', s => { s.seed.rect = { x: 100, y: 100, width: 1000, height: 700 } }, /offscreen/],
    ['offscreen-recovery', s => { s.seed.kind = 'saved' }, /offscreen/],
    ['resolution-shrink-clamp', s => { s.seed.rect = { x: 0, y: 0, width: 800, height: 600 } }, /overflowing/],
    ['overlay-position-restore', s => { s.observations[0].rect.x = 204 }, /overlay-origin/],
    ['overlay-position-restore', s => { s.observations[0].rect.y = 197 }, /overlay-origin/],
    ['overlay-position-restore', s => { s.observations.pop() }, /expanded/],
    ['overlay-position-restore', s => { s.observations[1].rect.height = 2000 }, /outside-workarea/],
    ['maximized-restore', s => { s.observations[1].zoomed = false; s.observations[1].rect = { x: 0, y: 0, width: 1920, height: 1040 } }, /maximized/],
  ]
  for (const [id, fn, pattern] of cases) { const r = report(); fn(scenario(r, id)); assert.throws(() => h.validate(r), pattern, id) }
})

test('a fabricated PASS for physical-display-change is rejected; BLOCKED needs a reason', () => {
  const evidence = { performedBy: 'owner', note: 'unplugged', before: [primary, second], after: [primary] }
  for (const mutate of [e => undefined, e => { e.performedBy = 'agent' }, e => { e.note = ' ' }, e => { e.after = e.before }, e => { e.after = [] }, e => { delete e.before }]) {
    const r = report(), s = scenario(r, 'physical-display-change'), e = structuredClone(evidence)
    s.status = 'PASS'; s.ownerEvidence = mutate === undefined ? undefined : (mutate(e), e)
    if (mutate.toString() === 'e => undefined') delete s.ownerEvidence
    assert.throws(() => h.validate(r), /fabricated/)
  }
  const r = report(); scenario(r, 'physical-display-change').reason = ''; assert.throws(() => h.validate(r), /gap-reason/)
  const noEvidence = report(); scenario(noEvidence, 'physical-display-change').status = 'PASS'; assert.throws(() => h.validate(noEvidence), /fabricated/)
})

test('FAIL dominates and any other BLOCKED is not PASS_WITH_GAPS', () => {
  const f = report(); Object.assign(scenario(f, 'maximized-restore'), { status: 'FAIL', reason: 'not-maximized' }); assert.equal(h.validate(f), 'FAIL')
  const b = report(); Object.assign(scenario(b, 'offscreen-recovery'), { status: 'BLOCKED', reason: 'x' }); assert.equal(h.validate(b), 'BLOCKED')
  const m = report(); Object.assign(scenario(m, 'offscreen-recovery'), { status: 'FAIL' }); assert.throws(() => h.validate(m), /gap-reason/)
})

test('bridge script targets the window-state probe and drift in the shared template fails loudly', () => {
  const script = h.bridgeScript(config)
  assert.match(script, /--window-state-probe-state=C:\\work\\desktop-native-window-state-state-a/)
  assert.match(script, /--window-state-probe-phase=restore/)
  assert.match(script, /windows-window-state-probe\.py/)
  assert.doesNotMatch(script, /overlay-probe-state|windows-overlay-probe\.py/)
  assert.match(script, /--scenario"? "?maximized-restore/)
  assert.match(script, /StartTime\.ToUniversalTime\(\)\.Ticks/)
  assert.match(h.recoveryScript(config), /recipients\.json/)
})

function fakeRun(t, options = {}) {
  const dir = fs.mkdtempSync(path.join(path.resolve(__dirname, '../..'), 'desktop-native-window-state-test-'))
  t.after(() => { fs.rmSync(dir, { recursive: true, force: true }); for (const d of fs.readdirSync(path.resolve(__dirname, '../..')).filter(d => d.startsWith('desktop-native-window-state-state-'))) fs.rmSync(path.resolve(__dirname, '../..', d), { recursive: true, force: true }) })
  const rep = report(), calls = []
  const toWindows = p => 'C:\\' + path.basename(p)
  const bridge = script => {
    calls.push(script)
    if (/Same-Process|Discover/.test(script) && /--scenario/.test(script)) {
      const id = /--scenario"? "?([\w-]+)/.exec(script)[1], launch = Number(/--launch"? "?(\d)/.exec(script)[1])
      const s = scenario(rep, id), probeDir = fs.readdirSync(dir).find(d => d === `${id}-${launch}`)
      const last = launch === (['persist-restore-secondary', 'maximized-restore'].includes(id) ? 2 : 1)
      const saved = s.observations.filter(o => (last ? o.phase !== 'saved' : o.phase === 'saved'))
      const body = options.blocked === id ? { status: 'BLOCKED', reason: 'single-monitor', observations: [], monitors: [primary] } : { status: 'PASS', hwnd: s.hwnd, observations: saved, monitors: s.monitors }
      fs.writeFileSync(path.join(dir, probeDir, 'probe.json'), JSON.stringify(body))
      return { error: null, electronExit: 0, pythonExit: body.status === 'PASS' ? 0 : 1, remaining: [], cleanupErrors: [], owned: [{ ...target, parent: 1, session: 1 }] }
    }
    return { remaining: [], cleanupErrors: [], stopped: [] }
  }
  return { dir, calls, result: h.run({ config, directory: dir, toWindows, bridge }), rep }
}

test('run composes launches, cleans every launch directory and reports PASS_WITH_GAPS', t => {
  const { result, calls, dir } = fakeRun(t)
  assert.equal(result.reason, undefined)
  assert.equal(result.status, 'PASS_WITH_GAPS')
  assert.equal(result.launches.length, 7)
  assert.equal(result.recovery.length, 7)
  assert.equal(calls.length, 14)
  assert.ok(dir)
})

test('run reports single-monitor as BLOCKED and a foreign host identity or dirty cleanup as FAIL', t => {
  assert.equal(fakeRun(t, { blocked: 'persist-restore-secondary' }).result.status, 'BLOCKED')
  const dir = fs.mkdtempSync(path.join(path.resolve(__dirname, '../..'), 'desktop-native-window-state-test-'))
  t.after(() => { fs.rmSync(dir, { recursive: true, force: true }); for (const d of fs.readdirSync(path.resolve(__dirname, '../..')).filter(d => d.startsWith('desktop-native-window-state-state-'))) fs.rmSync(path.resolve(__dirname, '../..', d), { recursive: true, force: true }) })
  const dirty = h.run({ config, directory: dir, toWindows: p => 'C:\\' + path.basename(p), bridge: () => ({ error: 'overlay-quit-timeout', remaining: [], cleanupErrors: [] }) })
  assert.equal(dirty.status, 'FAIL'); assert.match(dirty.reason, /timeout/)
})

test('CLI usage errors exit nonzero without launching anything', () => {
  const log = console.log; const out = []
  console.log = line => out.push(line)
  try {
    for (const argv of [['--bogus'], ['--evidence'], ['--evidence', 'a', 'b']]) {
      assert.equal(h.main(argv), 1)
    }
  } finally { console.log = log }
  const parsed = JSON.parse(out[0])
  assert.equal(parsed.status, 'BLOCKED'); assert.match(parsed.reason, /usage/)
  assert.deepEqual(parsed.scenarios.map(s => s.id), h.REQUIRED)
  assert.ok(parsed.scenarios.every(s => s.reason.trim()))
})
