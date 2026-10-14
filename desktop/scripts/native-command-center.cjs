'use strict'

// Independent CLI acceptance; import is inert. Shared bridge owns only Electron
// lineage and the Python probe, never user applications or Core.
const fs = require('node:fs')
const path = require('node:path')
const smoke = require('./native-smoke.cjs')
const overlay = require('./native-overlay.cjs')
const ROOT = path.resolve(__dirname, '../..')
const REQUIRED = ['windowed', 'maximize-restore', 'fullscreen-enter-exit', 'return-to-overlay', 'repeated-roundtrips']
function utc(script) { return script.replace(/\.StartTime\.Ticks/g, '.StartTime.ToUniversalTime().Ticks') }
function bridgeScript(config) {
  return utc(overlay.bridgeScript(config))
    .replaceAll('--overlay-probe-state=', '--command-center-probe-state=')
    .replaceAll('windows-overlay-probe.py', 'windows-command-center-probe.py')
    .replaceAll('overlay-exited-before-probe', 'command-center-exited-before-probe')
    .replaceAll('native-overlay-timeout', 'native-command-center-timeout')
}
function recoveryScript(config) { return utc(overlay.recoveryScript(config)) }
function validate(report, exists = () => false) {
  if (report?.platform !== 'win32' || report.realInput !== true || report.preflight?.ctypes !== true || !Array.isArray(report.scenarios) || report.scenarios.length !== REQUIRED.length || new Set(report.scenarios.map(s => s.id)).size !== REQUIRED.length) throw new Error('incomplete-native-report')
  if (!Array.isArray(report.cleanup?.remaining) || !Array.isArray(report.cleanup?.errors) || report.cleanup.remaining.length || report.cleanup.errors.length) throw new Error('cleanup-failed')
  for (const id of REQUIRED) {
    const s = report.scenarios.find(s => s.id === id)
    if (!s || !['PASS', 'FAIL', 'BLOCKED'].includes(s.status)) throw new Error('invalid-scenario')
    if (s.status !== 'PASS') { if (typeof s.reason !== 'string' || !s.reason.trim()) throw new Error('missing-gap-reason'); continue }
    if (!overlay.sameIdentity(s.target, s.target) || !/^\d+$/.test(s.hwnd) || BigInt(s.hwnd) <= 0n || !Array.isArray(s.observations) || !s.observations.length || s.observations.length > 32 || !Array.isArray(s.inputs) || s.inputs.length > 32 || !s.screenshots?.length || !s.screenshots.every(exists)) throw new Error('missing-native-evidence')
    for (const o of s.observations) {
      if (!overlay.sameIdentity(s.target, o.target) || s.hwnd !== o.centerHwnd || o.mounted !== true || o.nativeAgreement !== true || !Array.isArray(o.displays) || !o.displays.length || !o.nativeDisplay || !Number.isFinite(o.nativeDisplay.dpiX) || o.nativeDisplay.dpiX < 96 || !o.nativeDisplay.workArea || !o.nativeDisplay.monitor || !['COMMAND_CENTER_WINDOWED', 'COMMAND_CENTER_FULLSCREEN', 'OVERLAY_COMPACT', 'OVERLAY_EXPANDED'].includes(o.mode)) throw new Error('invalid-native-observation')
      if (![o.rect?.x, o.rect?.y, o.rect?.width, o.rect?.height].every(Number.isInteger) || o.rect.width <= 0 || o.rect.height <= 0) throw new Error('invalid-geometry')
    }
    for (const input of s.inputs) {
      overlay.coordinate(input)
      if (!overlay.sameIdentity(s.target, input.target) || !['mouse', 'escape'].includes(input.kind)) throw new Error('invalid-input')
    }
    const seen = phase => s.observations.some(o => o.phase === phase)
    const phases = {windowed:['initial', 'resized'], 'maximize-restore':['maximized', 'restored'], 'fullscreen-enter-exit':['fullscreen', 'button-exit', 'escape-exit'], 'return-to-overlay':['compact', 'expanded', 'compact-again'], 'repeated-roundtrips':['roundtrip-1', 'roundtrip-2', 'roundtrip-3']}[id]
    if (!phases.every(seen)) throw new Error('missing-scenario-phases')
    if (id !== 'windowed' && id !== 'maximize-restore' && !s.inputs.length) throw new Error('input-required')
    if (id === 'fullscreen-enter-exit' && !s.inputs.some(i => i.kind === 'escape')) throw new Error('escape-required')
    if (s.observations.some(o => o.mode.startsWith('OVERLAY_') && (o.centerVisible !== false || o.centerFullscreen !== false || o.overlayVisible !== true || o.overlayHit !== true))) throw new Error('hidden-center-or-overlay-hit-unproven')
    if (id === 'repeated-roundtrips' && !s.observations.every(o => o.windowCount === 2 && o.overlayHwnd === s.observations[0].overlayHwnd)) throw new Error('singleton-unproven')
    for (const o of s.observations) {
      const expected = o.phase.startsWith('fullscreen') ? 'COMMAND_CENTER_FULLSCREEN' : o.phase.startsWith('expanded') ? 'OVERLAY_EXPANDED' : /^(compact|overlay)/.test(o.phase) ? 'OVERLAY_COMPACT' : 'COMMAND_CENTER_WINDOWED'
      if (o.mode !== expected || o.centerFullscreen !== (expected === 'COMMAND_CENTER_FULLSCREEN') || (!expected.startsWith('OVERLAY_') && o.centerVisible !== true)) throw new Error('phase-mode-disagrees')
      if (expected === 'COMMAND_CENTER_FULLSCREEN' && Object.keys(o.rect).some(k => Math.abs(o.rect[k] - o.nativeDisplay.monitor[k]) > 2)) throw new Error('fullscreen-geometry-disagrees')
    }
    const at = phase => s.observations.find(o => o.phase === phase)
    const sameRect = (a,b) => Object.keys(a).every(k => a[k] === b[k])
    if (id === 'windowed' && sameRect(at('initial').rect, at('resized').rect)) throw new Error('resize-unobserved')
    if (id === 'maximize-restore' && (at('maximized').maximized !== true || at('restored').maximized !== false || !sameRect(s.observations[0].rect, at('restored').rect))) throw new Error('maximize-restore-unobserved')
    for (const i of s.inputs) {
      if (![s.hwnd, ...s.observations.map(o => o.overlayHwnd)].includes(i.hwnd) || (i.kind === 'escape' && i.hwnd !== s.hwnd) || (i.kind === 'mouse' && (!i.flags?.includes(2) || !i.flags?.includes(4)))) throw new Error('uncorrelated-or-incomplete-input')
    }
  }
  return report.scenarios.every(s => s.status === 'PASS') ? 'PASS' : report.scenarios.some(s => s.status === 'FAIL') ? 'FAIL' : 'BLOCKED'
}
// No required Command Center observation is third-party: the flag is bounded
// and syntactically supported but cannot waive a required scenario or a FAIL.
function acceptGaps(evidence, apps) {
  if (!Array.isArray(apps) || apps.length > 3 || new Set(apps).size !== apps.length || apps.some(a => !['explorer','vscode','terminal'].includes(a))) throw new Error('invalid-accepted-gaps')
  return evidence
}
function run({config, directory, bridge = overlay.nativeBridge, readReport = f => JSON.parse(smoke.readBoundedLog(f)), exists = f => {
  const p = smoke.localOutput(ROOT, f)
  if (path.dirname(p) !== directory || !/^screenshot-\d{2}\.bmp$/.test(path.basename(p))) return false
  const stat = fs.lstatSync(p)
  if (!stat.isFile() || stat.size < 54 || stat.size > 6400054) return false
  const fd = fs.openSync(p,'r'), header = Buffer.alloc(54)
  try { fs.readSync(fd,header,0,54,0); return header.subarray(0,2).toString() === 'BM' && header.readUInt32LE(2) === stat.size && header.readInt32LE(18) > 0 && header.readInt32LE(18) <= 1600 && Math.abs(header.readInt32LE(22)) > 0 && Math.abs(header.readInt32LE(22)) <= 1000 }
  finally { fs.closeSync(fd) }
}}) {
  const evidence = {status:'FAIL', directory:path.relative(ROOT, directory)}
  try {
    evidence.host = bridge(bridgeScript(config))
    evidence.native = readReport(path.join(directory, 'probe.json'))
    evidence.status = validate(evidence.native, exists)
    if (evidence.host.error || evidence.host.electronExit !== 0 || ![0,1].includes(evidence.host.pythonExit)) throw new Error(evidence.host.error || 'unobserved-native-exit')
    if (!Array.isArray(evidence.host.remaining) || !Array.isArray(evidence.host.cleanupErrors) || evidence.host.remaining.length || evidence.host.cleanupErrors.length) throw new Error('host-cleanup-failed')
    const owned = evidence.host.owned
    if (!Array.isArray(owned) || !owned.length || owned.length > 512 || new Set(owned.map(p => p.pid)).size !== owned.length || owned.some(p => !overlay.sameIdentity(p,p) || p.path !== config.exe)) throw new Error('invalid-host-ownership')
    for (const s of evidence.native.scenarios.filter(s => s.status === 'PASS')) {
      if (!owned.some(p => overlay.sameIdentity(p,s.target))) throw new Error('native-host-identity-disagrees')
    }
    if ((evidence.status === 'PASS') !== (evidence.host.pythonExit === 0)) throw new Error('probe-exit-verdict-mismatch')
  } catch (e) { evidence.status = 'FAIL'; evidence.reason = e.message }
  try {
    evidence.recovery = bridge(recoveryScript(config))
    if (!Array.isArray(evidence.recovery.remaining) || !Array.isArray(evidence.recovery.cleanupErrors) || evidence.recovery.remaining.length || evidence.recovery.cleanupErrors.length) throw new Error('recovery-cleanup-failed')
  } catch (e) { evidence.status = 'FAIL'; evidence.cleanupFailure = e.message }
  return evidence
}
function main(argv = process.argv.slice(2)) {
  let output, evidence = {status:'BLOCKED', scenarios:REQUIRED.map(id => ({id,status:'BLOCKED',reason:'preflight-not-run'}))}
  try {
    const gapArgs = argv.filter(a => a.startsWith('--accept-gaps='))
    if (gapArgs.length > 1) throw new Error('duplicate-accepted-gaps')
    const apps = gapArgs.length ? gapArgs[0].slice(14).split(',') : []
    acceptGaps(evidence, apps)
    const rest = argv.filter(a => !gapArgs.includes(a))
    if (rest.length && (rest.length !== 2 || rest[0] !== '--evidence')) throw new Error('usage: native-command-center.cjs [--evidence worktree-local.json] [--accept-gaps=explorer,vscode,terminal]')
    if (rest.length) output = smoke.localOutput(ROOT, rest[1])
    const exe = smoke.prerequisites(ROOT), win = p => smoke.command('wslpath', ['-w',p], 5000)
    const directory = fs.mkdtempSync(path.join(ROOT, 'desktop-native-command-center-')); fs.chmodSync(directory, 0o700)
    output ||= path.join(directory, 'report.json')
    const config = {exe:win(smoke.localRuntime(exe)), app:win(path.join(ROOT, 'desktop')), root:win(ROOT), directory:win(directory)}
    evidence = acceptGaps(run({config,directory}), apps)
  } catch (e) { evidence.reason = e.message; if (evidence.scenarios) evidence.scenarios.forEach(s => {s.reason = e.message}) }
  if (output) {
    try { fs.mkdirSync(path.dirname(output),{recursive:true}); fs.writeFileSync(output,JSON.stringify(evidence,null,2)) }
    catch (e) { evidence.status = 'FAIL'; evidence.evidenceError = e.message }
  }
  console.log(JSON.stringify(evidence)); return evidence.status === 'PASS' ? 0 : 1
}
if (require.main === module) process.exitCode = main()
module.exports = {REQUIRED, utc, bridgeScript, recoveryScript, validate, acceptGaps, run, main}
