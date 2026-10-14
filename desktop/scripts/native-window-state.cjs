'use strict'

// Independent CLI acceptance; import is inert. Launches the real app twice against a
// harness-owned userData directory (persistence ACTIVE) and observes Win32 geometry.
const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')
const smoke = require('./native-smoke.cjs')
const overlay = require('./native-overlay.cjs')
const center = require('./native-command-center.cjs')
const { PREFIX } = require('../src/window-state-probe')
const ROOT = path.resolve(__dirname, '../..')
const REQUIRED = ['persist-restore-secondary', 'offscreen-recovery', 'resolution-shrink-clamp', 'overlay-position-restore', 'maximized-restore', 'physical-display-change']
const PHYSICAL = 'physical-display-change'
const SEEDS = {
  'offscreen-recovery': { commandCenter: { displayId: 987654321, x: -30000, y: -30000, width: 1000, height: 700, maximized: false } },
  'resolution-shrink-clamp': { commandCenter: { displayId: null, x: 100, y: 100, width: 9000, height: 6000, maximized: false } },
  'overlay-position-restore': { overlay: { displayId: null, x: 200, y: 200, width: 420, height: 160 } },
}
const LAUNCHES = { 'persist-restore-secondary': ['seed', 'restore'], 'maximized-restore': ['seed', 'restore'] }
const RECT = ['x', 'y', 'width', 'height']
const isRect = r => Boolean(r) && RECT.every(k => Number.isInteger(r[k])) && r.width > 0 && r.height > 0
const inside = (r, a, slack = 0) => r.x >= a.x - slack && r.y >= a.y - slack && r.x + r.width <= a.x + a.width + slack && r.y + r.height <= a.y + a.height + slack
const disjoint = (r, a) => r.x + r.width <= a.x || a.x + a.width <= r.x || r.y + r.height <= a.y || a.y + a.height <= r.y

function swap(text, from, to) {
  if (!text.includes(from)) throw new Error('bridge-template-drift')
  return text.replace(from, () => to)
}
// config: {exe, app, root, directory, state, phase, scenario, launch} (Windows paths).
function bridgeScript(config) {
  const lit = value => smoke.windowsArg(value).replaceAll("'", "''")
  let script = center.utc(overlay.bridgeScript(config))
  script = swap(script, lit(`--overlay-probe-state=${config.directory}\\state`), `${lit(`--window-state-probe-state=${config.state}`)} ${lit(`--window-state-probe-phase=${config.phase}`)}`)
  script = swap(script, `${lit('--directory')} ${lit(config.directory)}`, `${lit('--directory')} ${lit(config.directory)} ${lit('--state')} ${lit(config.state)} ${lit('--scenario')} ${lit(config.scenario)} ${lit('--launch')} ${lit(String(config.launch))}`)
  return script.replaceAll('windows-overlay-probe.py', 'windows-window-state-probe.py')
    .replaceAll('overlay-exited-before-probe', 'window-state-exited-before-probe')
    .replaceAll('native-overlay-timeout', 'native-window-state-timeout')
}
function recoveryScript(config) { return center.recoveryScript(config) }

function validMonitors(list) {
  return Array.isArray(list) && list.length >= 1 && list.length <= 16 && new Set(list.map(m => m.handle)).size === list.length &&
    list.every(m => typeof m.handle === 'string' && m.handle.length > 0 && typeof m.primary === 'boolean' && isRect(m.monitor) && isRect(m.workArea))
}
function validateScenario(s) {
  if (!overlay.sameIdentity(s.target, s.target) || !/^\d+$/.test(s.hwnd) || BigInt(s.hwnd) <= 0n || !validMonitors(s.monitors) || !Array.isArray(s.observations) || !s.observations.length || s.observations.length > 32) throw new Error('missing-native-evidence')
  for (const o of s.observations) {
    const mon = s.monitors.find(m => m.handle === o.monitorHandle)
    if (!mon || !/^\d+$/.test(o.hwnd) || BigInt(o.hwnd) <= 0n || !isRect(o.rect) || typeof o.zoomed !== 'boolean' || typeof o.phase !== 'string' || !['overlay', 'command-center'].includes(o.host) ||
      !isRect(o.workArea) || RECT.some(k => o.workArea[k] !== mon.workArea[k])) throw new Error('invalid-native-observation')
    // A maximized window's GetWindowRect includes the invisible resize frame.
    if (!s.monitors.some(m => inside(o.rect, m.workArea, o.zoomed ? 16 : 0))) throw new Error('rect-outside-workarea')
  }
  const at = phase => s.observations.find(o => o.phase === phase)
  const restored = at('restored')
  if (!restored) throw new Error('restored-unobserved')
  if (s.id === 'persist-restore-secondary') {
    const saved = at('saved')
    if (s.monitors.length < 2) throw new Error('single-monitor')
    if (!saved || saved.monitorHandle !== restored.monitorHandle || s.monitors.find(m => m.handle === restored.monitorHandle).primary || RECT.some(k => Math.abs(saved.rect[k] - restored.rect[k]) > 2)) throw new Error('secondary-restore-mismatch')
  } else if (s.id === 'offscreen-recovery') {
    if (s.seed?.kind !== 'seeded' || !isRect(s.seed.rect) || !s.monitors.every(m => disjoint(s.seed.rect, m.monitor))) throw new Error('seed-not-offscreen')
  } else if (s.id === 'resolution-shrink-clamp') {
    if (s.seed?.kind !== 'seeded' || !isRect(s.seed.rect) || s.monitors.some(m => inside(s.seed.rect, m.workArea))) throw new Error('seed-not-overflowing')
  } else if (s.id === 'overlay-position-restore') {
    const expanded = at('expanded')
    if (s.seed?.kind !== 'seeded' || !isRect(s.seed.rect) || restored.host !== 'overlay' || Math.abs(restored.rect.x - s.seed.rect.x) > 2 || Math.abs(restored.rect.y - s.seed.rect.y) > 2) throw new Error('overlay-origin-mismatch')
    if (!expanded || expanded.host !== 'overlay') throw new Error('expanded-unobserved')
  } else if (s.id === 'maximized-restore') {
    if (restored.zoomed !== true) throw new Error('not-maximized-after-restore')
  }
}
function validate(report) {
  if (report?.platform !== 'win32' || report.preflight?.ctypes !== true || !Array.isArray(report.scenarios) || report.scenarios.length !== REQUIRED.length || new Set(report.scenarios.map(s => s.id)).size !== REQUIRED.length) throw new Error('incomplete-native-report')
  if (!Array.isArray(report.cleanup?.remaining) || !Array.isArray(report.cleanup?.errors) || report.cleanup.remaining.length || report.cleanup.errors.length) throw new Error('cleanup-failed')
  for (const id of REQUIRED) {
    const s = report.scenarios.find(row => row.id === id)
    if (!s || !['PASS', 'FAIL', 'BLOCKED'].includes(s.status)) throw new Error('invalid-scenario')
    if (s.status !== 'PASS') { if (typeof s.reason !== 'string' || !s.reason.trim()) throw new Error('missing-gap-reason'); continue }
    if (id === PHYSICAL) {
      const e = s.ownerEvidence
      if (e?.performedBy !== 'owner' || typeof e.note !== 'string' || !e.note.trim() || !validMonitors(e.before) || !validMonitors(e.after) || JSON.stringify(e.before) === JSON.stringify(e.after)) throw new Error('fabricated-physical-evidence')
      continue
    }
    validateScenario(s)
  }
  const rows = report.scenarios
  return rows.some(s => s.status === 'FAIL') ? 'FAIL' : rows.every(s => s.status === 'PASS') ? 'PASS'
    : rows.every(s => s.status === 'PASS' || s.id === PHYSICAL) ? 'PASS_WITH_GAPS' : 'BLOCKED'
}

function newestOwned(host) { return Array.isArray(host?.owned) && host.owned.length ? host.owned[0] : null }
function stateDirFor(id) { return path.join(ROOT, `${PREFIX}state-${id}-${crypto.randomBytes(4).toString('hex')}`) }
function run({ config, directory, toWindows, bridge = overlay.nativeBridge, readReport = f => JSON.parse(smoke.readBoundedLog(f)) }) {
  const evidence = { status: 'FAIL', directory: path.relative(ROOT, directory), launches: [] }
  const owned = [], scenarios = []
  const native = { platform: 'win32', preflight: { ctypes: false }, cleanup: { remaining: [], errors: [] }, scenarios }
  try {
    native.preflight.ctypes = true
    for (const id of REQUIRED.filter(r => r !== PHYSICAL)) {
      const stateDir = stateDirFor(id), seeded = Object.hasOwn(SEEDS, id)
      if (seeded) {
        fs.mkdirSync(stateDir, { mode: 0o700 })
        fs.writeFileSync(path.join(stateDir, 'window-state.json'), JSON.stringify(SEEDS[id]))
      }
      const phases = seeded ? ['restore'] : LAUNCHES[id]
      let scenario = { id, status: 'PASS', observations: [], seed: seeded ? { kind: 'seeded', rect: Object.values(SEEDS[id])[0] } : { kind: 'saved' } }
      if (seeded) { const { x, y, width, height } = scenario.seed.rect; scenario.seed.rect = { x, y, width, height } }
      for (const [index, phase] of phases.entries()) {
        const launchDir = path.join(directory, `${id}-${index + 1}`)
        for (const f of ['request.json', 'response.json', 'response.json.tmp']) fs.rmSync(path.join(stateDir, f), { force: true })
        fs.mkdirSync(launchDir, { mode: 0o700 })
        const host = bridge(bridgeScript({ ...config, directory: toWindows(launchDir), state: toWindows(stateDir), phase, scenario: id, launch: index + 1 }))
        evidence.launches.push({ id, launch: index + 1, host })
        if (host.error || host.electronExit !== 0 || ![0, 1].includes(host.pythonExit) || !Array.isArray(host.remaining) || !Array.isArray(host.cleanupErrors) || host.remaining.length || host.cleanupErrors.length) throw new Error(host.error || `launch-failed:${id}:${index + 1}`)
        owned.push(...(host.owned || []))
        const probe = readReport(path.join(launchDir, 'probe.json'))
        scenario.observations.push(...(probe.observations || []))
        scenario.monitors = probe.monitors
        scenario.hwnd = probe.hwnd
        scenario.target = newestOwned(host)
        if (probe.status !== 'PASS') { scenario = { id, status: probe.status === 'BLOCKED' ? 'BLOCKED' : 'FAIL', reason: probe.reason || 'probe-reported-no-reason' }; break }
        if (index === phases.length - 1 && (host.pythonExit !== 0)) throw new Error('probe-exit-verdict-mismatch')
      }
      scenarios.push(scenario)
    }
    // ponytail: a physical monitor change needs the owner at the machine; owner evidence is attached to the report by hand.
    scenarios.push({ id: PHYSICAL, status: 'BLOCKED', reason: 'requires-owner-physical-display-change' })
    evidence.native = native
    evidence.status = validate(native)
    for (const s of scenarios.filter(s => s.status === 'PASS' && s.id !== PHYSICAL)) {
      if (!owned.some(p => overlay.sameIdentity(p, s.target))) throw new Error('native-host-identity-disagrees')
    }
  } catch (e) { evidence.status = 'FAIL'; evidence.reason = e.message; evidence.native = native }
  try {
    evidence.recovery = []
    for (const launch of fs.readdirSync(directory)) {
      const dir = path.join(directory, launch)
      if (!fs.statSync(dir).isDirectory()) continue
      const result = bridge(recoveryScript({ ...config, directory: toWindows(dir) }))
      evidence.recovery.push(result)
      if (!Array.isArray(result.remaining) || !Array.isArray(result.cleanupErrors) || result.remaining.length || result.cleanupErrors.length) throw new Error('recovery-cleanup-failed')
    }
  } catch (e) { evidence.status = 'FAIL'; evidence.cleanupFailure = e.message }
  return evidence
}
function main(argv = process.argv.slice(2)) {
  let output, evidence = { status: 'BLOCKED', reason: 'not-run', scenarios: REQUIRED.map(id => ({ id, status: 'BLOCKED', reason: 'preflight-not-run' })) }
  try {
    if (argv.length && (argv.length !== 2 || argv[0] !== '--evidence')) throw new Error('usage: native-window-state.cjs [--evidence worktree-local.json]')
    if (argv.length) output = smoke.localOutput(ROOT, argv[1])
    const exe = smoke.prerequisites(ROOT), toWindows = p => smoke.command('wslpath', ['-w', p], 5000)
    const directory = fs.mkdtempSync(path.join(ROOT, `${PREFIX}run-`)); fs.chmodSync(directory, 0o700)
    output ||= path.join(directory, 'report.json')
    const config = { exe: toWindows(smoke.localRuntime(exe)), app: toWindows(path.join(ROOT, 'desktop')), root: toWindows(ROOT) }
    evidence = run({ config, directory, toWindows })
  } catch (e) { evidence.reason = e.message; evidence.scenarios?.forEach(s => { s.reason = e.message }) }
  if (output) {
    try { fs.mkdirSync(path.dirname(output), { recursive: true }); fs.writeFileSync(output, JSON.stringify(evidence, null, 2)) }
    catch (e) { evidence.status = 'FAIL'; evidence.evidenceError = e.message }
  }
  console.log(JSON.stringify(evidence)); return ['PASS', 'PASS_WITH_GAPS'].includes(evidence.status) ? 0 : 1
}
if (require.main === module) process.exitCode = main()
module.exports = { REQUIRED, SEEDS, bridgeScript, recoveryScript, validate, run, main }
