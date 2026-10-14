'use strict'

// WSL orchestrator: runs every native harness, then scores one fixed scenario
// list from parsed evidence only. Nothing is PASS without an evidence file.
const fs = require('node:fs')
const path = require('node:path')
const { spawnSync } = require('node:child_process')
const { powershell, psLiteral, prerequisites, localOutput } = require('./native-smoke.cjs')
const ROOT = path.resolve(__dirname, '../..')
const SCENARIOS = ['shell-launch', 'overlay-transparent-click-through', 'command-center-modes', 'fullscreen-escape', 'window-state-restore-multi-monitor', 'dpi-scaling-topology', 'core-attach-restart', 'tray-shortcuts-login', 'no-orphan-processes']
const HARNESSES = ['smoke', 'overlay', 'command-center', 'window-state', 'core-lifecycle', 'tray']
const HARNESS_SCRIPT = { smoke: 'native-smoke.cjs', overlay: 'native-overlay.cjs', 'command-center': 'native-command-center.cjs', 'window-state': 'native-window-state.cjs', 'core-lifecycle': 'native-core-lifecycle.cjs', tray: 'native-tray.cjs' }
const STATUSES = ['PASS', 'FAIL', 'BLOCKED']
const USAGE = 'usage: native-qa-matrix.cjs [--evidence worktree-local.json]'

function parseArgs(argv, root = ROOT) {
  if (!argv.length) return { evidence: null }
  if (argv.length !== 2 || argv[0] !== '--evidence' || !argv[1]) throw new Error(USAGE)
  return { evidence: localOutput(root, argv[1]) }
}

function aggregate(rows) {
  if (rows.some(r => r.status === 'FAIL')) return 'FAIL'
  if (rows.length !== SCENARIOS.length || SCENARIOS.some(id => rows.filter(r => r.id === id).length !== 1) || rows.some(r => !['PASS', 'BLOCKED'].includes(r.status))) return 'FAIL'
  return rows.some(r => r.status === 'BLOCKED') ? 'BLOCKED' : 'PASS'
}

const dpiOf = m => m.dpiX ?? m.dpi ?? null
function classifyTopology(topology) {
  const monitors = Array.isArray(topology?.monitors) ? topology.monitors : []
  const dpis = monitors.map(dpiOf)
  const known = dpis.every(d => Number.isFinite(d) && d > 0)
  return {
    count: monitors.length,
    multiMonitor: monitors.length >= 2,
    primaryCount: monitors.filter(m => m.primary === true).length,
    dpiKnown: monitors.length > 0 && known,
    mixedDpi: known && new Set(dpis).size > 1,
    scales: known ? dpis.map(d => Math.round((d / 96) * 100)) : []
  }
}

// Normalise any harness evidence into { status, reason, rows } or null when unusable.
function normalise(evidence) {
  if (!evidence || typeof evidence !== 'object' || typeof evidence.status !== 'string') return null
  let status = evidence.status
  let reason = evidence.reason ?? evidence.evidenceError ?? evidence.cleanupFailure
  if (status === 'PASS_WITH_GAPS') {
    status = 'BLOCKED'
    const gaps = (evidence.acceptedGaps ?? (evidence.scenarios ?? evidence.native?.scenarios ?? []).filter(s => s.status !== 'PASS')).map(g => g.id ?? g).join(',')
    reason = `accepted-gaps${gaps ? `:${gaps}` : ''}`
  }
  if (!STATUSES.includes(status)) return null
  const rows = evidence.scenarios ?? evidence.native?.scenarios ?? null
  return { status, reason, rows: Array.isArray(rows) ? rows : null }
}

function blockedRows(rows) {
  const r = (rows ?? []).filter(s => s.status === 'BLOCKED')
  return r.length ? r.map(s => `${s.id}${s.reason ? `(${s.reason})` : ''}`).join(',') : undefined
}

// result: { exitCode, evidence } where evidence is the parsed JSON or null/undefined.
function mapHarness(name, result) {
  const base = { harness: name }
  const exitCode = result?.exitCode
  const n = normalise(result?.evidence)
  if (!n) return { ...base, status: 'FAIL', reason: result?.evidence ? 'evidence-unparseable-status' : `no-evidence(exit=${exitCode ?? 'none'})` }
  if (n.status === 'FAIL') return { ...base, status: 'FAIL', reason: n.reason ?? `harness-fail(exit=${exitCode})` }
  if (n.status === 'BLOCKED') return { ...base, status: 'BLOCKED', reason: n.reason ?? blockedRows(n.rows) ?? 'harness-blocked' }
  // PASS needs a zero exit and no non-PASS row inside the evidence.
  if (exitCode !== 0) return { ...base, status: 'FAIL', reason: `pass-evidence-with-exit-${exitCode}` }
  const bad = (n.rows ?? []).filter(s => s.status !== 'PASS')
  if (bad.length) return { ...base, status: 'FAIL', reason: `pass-with-nonpass-rows:${bad.map(s => s.id).join(',')}` }
  return { ...base, status: 'PASS' }
}

function rowOf(result, id) {
  const n = normalise(result?.evidence)
  return n?.rows?.find(s => s.id === id)
}

function scoreScenarios({ topology, topologyError, results, orphans, orphanError }) {
  const m = Object.fromEntries(HARNESSES.map(h => [h, mapHarness(h, results[h])]))
  const from = (id, h, extra) => ({ id, ...(extra ?? m[h]), evidence: h })
  const out = [
    from('shell-launch', 'smoke'),
    from('overlay-transparent-click-through', 'overlay'),
    from('command-center-modes', 'command-center')
  ]
  // Fullscreen: specific row if the harness reported one, else inherit the harness verdict.
  const cc = m['command-center']
  const fs = rowOf(results['command-center'], 'fullscreen-enter-exit')
  let fsRes = cc
  if (cc.status === 'PASS') fsRes = fs?.status === 'PASS' ? cc : { status: 'FAIL', reason: 'fullscreen-row-missing-or-not-pass' }
  else if (cc.status === 'BLOCKED' && fs?.status === 'PASS') fsRes = { status: 'PASS' }
  out.push({ id: 'fullscreen-escape', status: fsRes.status, reason: fsRes.reason, evidence: 'command-center' })

  const topo = topology ? classifyTopology(topology) : null
  const ws = m['window-state']
  const wsRes = ws.status === 'FAIL' ? ws
    : !topo ? { status: 'FAIL', reason: `topology-unavailable:${topologyError ?? 'unknown'}` }
      : !topo.multiMonitor ? { status: 'BLOCKED', reason: `single-monitor(count=${topo.count})` }
        : ws
  out.push({ id: 'window-state-restore-multi-monitor', status: wsRes.status, reason: wsRes.reason, evidence: 'window-state' })

  let dpi
  if (!topo) dpi = { status: 'FAIL', reason: `topology-unavailable:${topologyError ?? 'unknown'}` }
  else if (!topo.count || topo.primaryCount !== 1) dpi = { status: 'FAIL', reason: `topology-invalid(count=${topo.count},primary=${topo.primaryCount})` }
  else if (!topo.dpiKnown) dpi = { status: 'BLOCKED', reason: 'per-monitor-dpi-unavailable' }
  else if (!topo.multiMonitor) dpi = { status: 'BLOCKED', reason: `single-monitor(scale=${topo.scales[0]}%)` }
  else dpi = { status: 'PASS' }
  out.push({ id: 'dpi-scaling-topology', ...dpi, evidence: 'topology' })

  out.push(from('core-attach-restart', 'core-lifecycle'))
  out.push(from('tray-shortcuts-login', 'tray'))

  let orphan
  if (!Array.isArray(orphans)) orphan = { status: 'FAIL', reason: `orphan-scan-failed:${orphanError ?? 'unknown'}` }
  else if (orphans.length) orphan = { status: 'FAIL', reason: `orphans:${orphans.map(p => `${p.name}#${p.pid}`).join(',')}` }
  else orphan = { status: 'PASS' }
  out.push({ id: 'no-orphan-processes', ...orphan, evidence: 'process-scan' })

  return { scenarios: out.map(s => (s.reason === undefined ? (({ reason, ...rest }) => rest)(s) : s)), topology: topo }
}

// Python ctypes, not PowerShell Add-Type: WDAC hosts run PowerShell in ConstrainedLanguage mode.
const TOPOLOGY_PY = `import ctypes as C, ctypes.wintypes as W, json, sys
u, s = C.windll.user32, C.windll.shcore
try: u.SetProcessDpiAwarenessContext(C.c_void_p(-4))
except Exception: pass
class MI(C.Structure): _fields_ = [('cb', W.DWORD), ('rc', W.RECT), ('work', W.RECT), ('flags', W.DWORD), ('device', W.WCHAR * 32)]
u.GetMonitorInfoW.argtypes = [W.HMONITOR, C.c_void_p]
s.GetDpiForMonitor.argtypes = [W.HMONITOR, C.c_int, C.POINTER(W.UINT), C.POINTER(W.UINT)]
mons = []
def cb(h, dc, r, p):
    i = MI(); i.cb = C.sizeof(i)
    if not u.GetMonitorInfoW(h, C.byref(i)): return True
    x, y = W.UINT(), W.UINT(); ok = s.GetDpiForMonitor(h, 0, C.byref(x), C.byref(y)) == 0
    rc = i.rc
    mons.append(dict(device=i.device, primary=bool(i.flags & 1), x=rc.left, y=rc.top, width=rc.right - rc.left, height=rc.bottom - rc.top, dpiX=x.value if ok else None, dpiY=y.value if ok else None))
    return True
u.EnumDisplayMonitors(None, None, C.WINFUNCTYPE(W.BOOL, W.HMONITOR, W.HDC, C.POINTER(W.RECT), W.LPARAM)(cb), 0)
v = sys.getwindowsversion()
print(json.dumps(dict(monitors=mons, windowsVersion=f'{v.major}.{v.minor}.{v.build}')))`
const TOPOLOGY_SCRIPT = `$ErrorActionPreference='Stop'
$py=(Get-Command python.exe).Source
@'
${TOPOLOGY_PY}
'@ | & $py -`

const orphanScript = windowsRoot => `$ErrorActionPreference='Stop'
$root=${psLiteral(windowsRoot)}
$rows=@(Get-CimInstance Win32_Process | Where-Object { ($_.Name -in @('electron.exe','jarvis-desktop.exe')) -and ( ($_.ExecutablePath -like '*\\jarvis\\electron-runtime\\*') -or ($_.CommandLine -like ('*' + $root + '*')) ) } | ForEach-Object { @{ pid=[int]$_.ProcessId; name=$_.Name } })
ConvertTo-Json -InputObject @($rows) -Compress`

function runHarness(name, file, { timeoutMs = 20 * 60 * 1000 } = {}) {
  fs.rmSync(file, { force: true })
  const r = spawnSync(process.execPath, [path.join(__dirname, HARNESS_SCRIPT[name]), '--evidence', file], { encoding: 'utf8', timeout: timeoutMs, maxBuffer: 32 * 1024 * 1024, shell: false, stdio: ['ignore', 'pipe', 'pipe'] })
  let evidence = null, parseError
  try { evidence = JSON.parse(fs.readFileSync(file, 'utf8')) } catch (e) { parseError = e.code === 'ENOENT' ? 'missing' : 'unparseable' }
  return { exitCode: r.status ?? (r.error ? -1 : null), signal: r.signal, spawnError: r.error?.code, evidence, evidenceFile: path.relative(ROOT, file), parseError }
}

function main(argv = process.argv.slice(2), env = {}) {
  let args
  try { args = parseArgs(argv) } catch (e) { console.error(e.message); return 2 }
  const report = { status: 'FAIL', generatedAt: new Date().toISOString(), scenarios: [] }
  const ctx = { results: {} }
  const dir = fs.mkdtempSync(path.join(ROOT, 'desktop-native-qa-matrix-')); fs.chmodSync(dir, 0o700)
  try {
    try { prerequisites(ROOT) } catch (e) { report.prerequisiteError = e.message }
    try { ctx.topology = powershell(TOPOLOGY_SCRIPT) } catch (e) { ctx.topologyError = String(e.message).slice(0, 300) }
    for (const h of HARNESSES) {
      const res = runHarness(h, path.join(dir, `${h}.json`), env)
      ctx.results[h] = res
      console.error(`[qa-matrix] ${h}: exit=${res.exitCode} evidence=${res.evidence ? res.evidence.status : res.parseError}`)
    }
    try { ctx.orphans = powershell(orphanScript(require('./native-smoke.cjs').command('wslpath', ['-w', ROOT], 5000))) } catch (e) { ctx.orphanError = String(e.message).slice(0, 300) }
    const scored = scoreScenarios(ctx)
    report.scenarios = scored.scenarios
    report.topology = { ...ctx.topology, classification: scored.topology, error: ctx.topologyError }
    report.harnesses = Object.fromEntries(HARNESSES.map(h => { const r = ctx.results[h]; return [h, { exitCode: r.exitCode, signal: r.signal, evidenceFile: r.evidenceFile, status: r.evidence?.status ?? null, parseError: r.parseError }] }))
    report.orphans = ctx.orphans ?? null
    report.status = aggregate(report.scenarios)
  } catch (e) { report.reason = e.message }
  if (args.evidence) {
    try { fs.mkdirSync(path.dirname(args.evidence), { recursive: true }); fs.writeFileSync(args.evidence, JSON.stringify(report, null, 2)) }
    catch (e) { report.status = 'FAIL'; report.evidenceError = e.message }
  }
  console.log(JSON.stringify(report))
  return report.status === 'PASS' ? 0 : 1
}

if (require.main === module) process.exitCode = main()
module.exports = { SCENARIOS, HARNESSES, parseArgs, aggregate, classifyTopology, mapHarness, scoreScenarios, normalise, main }
