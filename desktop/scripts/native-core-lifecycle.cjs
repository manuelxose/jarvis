'use strict'

// Linux Node orchestrates Windows PowerShell directly (same bridge as native-smoke).
// Starts a harness-owned Jarvis Core on private ports, attaches the real desktop
// to it, restarts Core, and proves the desktop neither spawns nor stops Core.
const fs = require('node:fs')
const path = require('node:path')
const { command, powershell, psLiteral, prerequisites, localRuntime } = require('./native-smoke.cjs')

const ROOT = path.resolve(__dirname, '../..')
const PROTECTED_PORTS = [47811, 47812]
const DEFAULT_CONTROL_PORT = 47921
const DEFAULT_EVENTS_PORT = 47922
const SCENARIOS = ['core-start', 'desktop-attach', 'core-quit-desktop-survives', 'core-relaunch-reattach', 'desktop-exit-core-survives', 'core-cleanup', 'no-python-spawn']
const MARKER_PREFIX = '[jarvis-core] '

function parsePort(value, label) {
  if (!/^\d{4,5}$/.test(String(value))) throw new Error(`invalid-port:${label}`)
  const port = Number(value)
  if (port < 1024 || port > 65535 || PROTECTED_PORTS.includes(port)) throw new Error(`invalid-port:${label}`)
  return port
}
function parseArgs(argv) {
  const out = { controlPort: DEFAULT_CONTROL_PORT, eventsPort: DEFAULT_EVENTS_PORT, evidence: null }
  for (let i = 0; i < argv.length; i += 2) {
    const [flag, value] = [argv[i], argv[i + 1]]
    if (value === undefined) throw new Error('usage: native-core-lifecycle.cjs [--control-port N] [--events-port N] [--evidence file.json]')
    if (flag === '--control-port') out.controlPort = parsePort(value, 'control')
    else if (flag === '--events-port') out.eventsPort = parsePort(value, 'events')
    else if (flag === '--evidence') out.evidence = value
    else throw new Error(`unknown-argument:${flag}`)
  }
  if (out.controlPort === out.eventsPort) throw new Error('invalid-port:control-equals-events')
  return out
}

// Only these keys may differ from the real Windows config.
const DERIVED_KEYS = ['daemon.control_port', 'daemon.ui_events_port', 'memory.db_path']
function deriveConfig(base, { controlPort, eventsPort, dbPath }) {
  // memory is optional in Jarvis config (config.py defaults); daemon is required.
  if (!base || typeof base !== 'object' || typeof base.daemon !== 'object' || (base.memory !== undefined && typeof base.memory !== 'object')) throw new Error('base-config-malformed')
  if (typeof dbPath !== 'string' || !dbPath) throw new Error('db-path-required')
  const config = JSON.parse(JSON.stringify(base))
  const changed = []
  const set = (section, key, value) => { changed.push({ key: `${section}.${key}`, from: base[section]?.[key] ?? null, to: value }); (config[section] ??= {})[key] = value }
  set('daemon', 'control_port', controlPort)
  set('daemon', 'ui_events_port', eventsPort)
  set('memory', 'db_path', dbPath)
  return { config, changed }
}

function venvCandidates(root, env = process.env) {
  const enclosing = path.resolve(root, '../..')
  // JARVIS_WIN_PYTHON (WSL path to a Windows python.exe) is checked first: the project .venv may be a WSL venv.
  return [...(env.JARVIS_WIN_PYTHON ? [env.JARVIS_WIN_PYTHON] : []), path.join(root, '.venv-win/Scripts/python.exe'), path.join(root, '.venv/Scripts/python.exe'), path.join(enclosing, '.venv-win/Scripts/python.exe'), path.join(enclosing, '.venv/Scripts/python.exe')]
}
function locateVenv(root, exists = fs.existsSync, env = process.env) {
  const checked = venvCandidates(root, env)
  return { found: checked.find(p => exists(p)) || null, checked }
}

function parseCoreMarkers(text) {
  const records = []
  for (const line of String(text).split(/\r?\n/)) {
    if (!line.startsWith(MARKER_PREFIX)) continue
    try { const record = JSON.parse(line.slice(MARKER_PREFIX.length)); if (record && typeof record.state === 'string') records.push(record) } catch { /* partial line */ }
  }
  return records
}
function markersLeakSecret(records) {
  return records.some(r => Object.keys(r).some(k => /token/i.test(k)) || Object.values(r).some(v => typeof v === 'string' && /[A-Za-z0-9_-]{40,}/.test(v)))
}
// Returns the state names, consecutive duplicates collapsed.
function stateSequence(records) {
  const out = []
  for (const r of records) if (out[out.length - 1] !== r.state) out.push(r.state)
  return out
}
// phase 'attach': connected seen. 'loss': after a connected, reconnecting/disconnected seen.
// 'reattach': after that loss, connected seen again. Ordered, not merely present.
function evaluateSequence(records, phase) {
  const seq = stateSequence(records)
  const connected = seq.indexOf('connected')
  if (phase === 'attach') return connected >= 0
  const loss = seq.findIndex((s, i) => i > connected && connected >= 0 && (s === 'reconnecting' || s === 'disconnected'))
  if (phase === 'loss') return loss >= 0
  return loss >= 0 && seq.indexOf('connected', loss + 1) > loss
}

function scenarioResult(id, status, evidence = {}) {
  if (!['PASS', 'FAIL', 'BLOCKED'].includes(status)) throw new Error('invalid-status')
  return { id, status, ...evidence }
}
function aggregate(scenarios) {
  if (scenarios.some(s => s.status === 'FAIL')) return 'FAIL'
  if (scenarios.some(s => s.status === 'BLOCKED')) return 'BLOCKED'
  return scenarios.length === SCENARIOS.length && SCENARIOS.every(id => scenarios.some(s => s.id === id && s.status === 'PASS')) ? 'PASS' : 'FAIL'
}
function blockedReport(reason, detail = {}) {
  const scenarios = SCENARIOS.map(id => scenarioResult(id, 'BLOCKED', { reason }))
  return { status: aggregate(scenarios), reason, ...detail, scenarios }
}
function exitCodeFor(status, strict = false) { return status === 'PASS' ? 0 : status === 'BLOCKED' ? (strict ? 3 : 0) : 1 }

// -- PowerShell steps. Arguments travel as JSON, never interpolated into script text.
const PRELUDE = arg => `$ErrorActionPreference = 'Stop'\n$a = ConvertFrom-Json ${psLiteral(JSON.stringify(arg))}\n`
const IDENTITY = `
function Alive($r) { $p = Get-Process -Id $r.pid -ErrorAction SilentlyContinue; return ($null -ne $p -and "$($p.StartTime.Ticks)" -eq $r.ticks) }
function Tree($rootPid, $rootTicks) {
  $all = @(Get-CimInstance Win32_Process); $set = @([int]$rootPid); $out = @(); $changed = $true
  while ($changed) { $changed = $false; foreach ($r in $all) {
    if (($set -contains [int]$r.ParentProcessId) -and -not ($set -contains [int]$r.ProcessId)) {
      $p = Get-Process -Id $r.ProcessId -ErrorAction SilentlyContinue
      if ($null -ne $p -and $p.StartTime.Ticks -ge [int64]$rootTicks) { $set += [int]$r.ProcessId; $changed = $true }
    } } }
  foreach ($r in $all) { if ($set -contains [int]$r.ProcessId) { $p = Get-Process -Id $r.ProcessId -ErrorAction SilentlyContinue; if ($null -ne $p) { $out += @{ pid=[int]$r.ProcessId; parent=[int]$r.ParentProcessId; name=$r.Name; ticks="$($p.StartTime.Ticks)" } } } }
  return ,$out
}
`
const CONTROL_PY = `import hashlib, json, os, socket
a = json.loads(os.environ['JARVIS_HARNESS_CONTROL'])
try:
    with socket.create_connection(('127.0.0.1', int(a['port'])), timeout=3) as c:
        c.settimeout(4); c.sendall((a['cmd'] + '\\n').encode())
        data = b''
        while not data.endswith(b'\\n') and len(data) <= 65536:
            chunk = c.recv(4096)
            if not chunk: break
            data += chunk
    obj = json.loads(data.decode().strip())
    sha = None
    if isinstance(obj, dict) and obj.get('token'):
        sha = hashlib.sha256(str(obj['token']).encode()).hexdigest()[:12]
        obj.pop('token', None); obj.pop('app_url', None)
    out = {'ok': True, 'reply': obj, 'tokenSha': sha}
except Exception as e:
    out = {'ok': False, 'error': str(e) or type(e).__name__}
print(json.dumps(out))`
const step = {
  ports: ports => `${PRELUDE({ ports })}$r = @(); foreach ($c in @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $a.ports -contains $_.LocalPort })) { $r += @{ port=[int]$c.LocalPort; pid=[int]$c.OwningProcess } }\nConvertTo-Json -InputObject @($r) -Compress`,
  // Core and desktop via WMI, outside the bridge's process tree: started by Start-Process here they keep
  // the WSL interop call open until they exit (bridge-ETIMEDOUT). cmd.exe sets env and redirects output.
  spawnDetached: arg => `${PRELUDE(arg)}$all = @($a.exe, $a.cwd, $a.stdout, $a.stderr) + @($a.args) + @($a.env | ForEach-Object { $_.name; $_.value })
if (@($all | Where-Object { "$_" -match '["%\r\n]' }).Count) { throw 'spawn-unsafe-value' }
$sets = (@($a.env) | ForEach-Object { 'set "' + $_.name + '=' + $_.value + '"&& ' }) -join ''
$argv = (@($a.args) | ForEach-Object { '"' + $_ + '"' }) -join ' '
$line = 'cmd.exe /d /s /c "' + $sets + '"' + $a.exe + '" ' + $argv + ' 1>"' + $a.stdout + '" 2>"' + $a.stderr + '""'
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $line; CurrentDirectory = $a.cwd }
if ($r.ReturnValue -ne 0) { throw ('spawn-wmi-' + $r.ReturnValue) }
$p = Get-Process -Id $r.ProcessId
@{ pid=$p.Id; ticks="$($p.StartTime.Ticks)"; path=$p.Path } | ConvertTo-Json -Compress`,
  // Control-socket client in Python (ConstrainedLanguage blocks TcpClient); the token is replaced
  // by a short hash before leaving Windows. Arguments travel as JSON in an env var, never as code.
  control: arg => `${PRELUDE(arg)}$env:JARVIS_HARNESS_CONTROL = ($a | ConvertTo-Json -Compress)
$py = (Get-Command python.exe).Source
@'
${CONTROL_PY}
'@ | & $py -`,
  alive: records => `${PRELUDE({ records })}${IDENTITY}$r = @(); foreach ($x in @($a.records)) { if (Alive $x) { $r += @{ pid=[int]$x.pid; ticks=$x.ticks } } }\nConvertTo-Json -InputObject @($r) -Compress`,
  tree: root => `${PRELUDE(root)}${IDENTITY}if (-not (Alive $a)) { '[]' } else { ConvertTo-Json -InputObject @(Tree $a.pid $a.ticks) -Depth 4 -Compress }`,
  stop: records => `${PRELUDE({ records })}${IDENTITY}$stopped = @()
foreach ($x in @($a.records)) { if (Alive $x) { try { Stop-Process -Id $x.pid -Force -ErrorAction Stop; $stopped += [int]$x.pid } catch {} } }
$end = (Get-Date).AddSeconds(5); $left = @()
do { $left = @(); foreach ($x in @($a.records)) { if (Alive $x) { $left += @{ pid=[int]$x.pid; ticks=$x.ticks } } }; if ($left.Count -eq 0) { break }; Start-Sleep -Milliseconds 100 } while ((Get-Date) -lt $end)
@{ stopped=@($stopped); remaining=@($left) } | ConvertTo-Json -Depth 4 -Compress`,
}

// flat(): ConvertTo-Json -InputObject @(Tree ...) nests the process list one level ([[...]]).
const asArray = value => (Array.isArray(value) ? value.flat() : value ? [value] : [])
function sleepSync(ms) { Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms) }
function waitFor(probe, timeoutMs, { sleep = sleepSync, now = Date.now, intervalMs = 500 } = {}) {
  const end = now() + timeoutMs
  for (;;) {
    const value = probe()
    if (value) return value
    if (now() >= end) return null
    sleep(intervalMs)
  }
}

// ctx: { ps, readText, ports, controlPort, eventsPort, python, src, electron, app, dirs: {local, win, data}, baseConfig, sleep, now }
function runLifecycle(ctx) {
  const results = []
  const owned = new Map() // pid:ticks -> record
  const remember = (role, rec) => { owned.set(`${rec.pid}:${rec.ticks}`, { role, pid: rec.pid, ticks: rec.ticks }); return rec }
  const snapshotTree = root => { for (const p of asArray(ctx.ps(step.tree(root)))) if (!owned.has(`${p.pid}:${p.ticks}`)) owned.set(`${p.pid}:${p.ticks}`, { role: 'descendant', pid: p.pid, ticks: p.ticks, name: p.name }) }
  const control = cmd => ctx.ps(step.control({ port: ctx.controlPort, cmd }))
  const logPath = name => path.join(ctx.dirs.local, name)
  const desktopMarkers = () => { try { return parseCoreMarkers(ctx.readText(logPath('desktop.out.log'))) } catch { return [] } }
  const record = (id, status, evidence) => { const r = scenarioResult(id, status, evidence); results.push(r); return r }
  const blockRest = reason => { for (const id of SCENARIOS) if (!results.some(r => r.id === id)) record(id, 'BLOCKED', { reason }) }
  const startCore = file => {
    const core = remember('core', ctx.ps(step.spawnDetached({
      exe: ctx.python, args: ['-m', 'jarvis', 'daemon', '--config', ctx.dirs.win + '\\config.lifecycle.json'], cwd: ctx.dirs.win,
      stdout: ctx.dirs.winLogs + `\\${file}.out.log`, stderr: ctx.dirs.winLogs + `\\${file}.err.log`,
      env: [{ name: 'PYTHONPATH', value: ctx.src }, { name: 'LOCALAPPDATA', value: ctx.dirs.win + '\\data' }, { name: 'PYTHONUNBUFFERED', value: '1' }],
    })))
    return core
  }
  const waitUi = () => waitFor(() => { const r = control('ui'); return r.ok && r.reply?.ok === true && r.tokenSha ? r : null }, 120000, ctx)

  let core1, core2, desktop, tokenA
  try {
    const listeners = asArray(ctx.ps(step.ports(ctx.ports)))
    if (listeners.length) { blockRest(`port-owned-by-non-harness-process:${listeners.map(l => l.port).join(',')}`); return finish(results, owned, ctx) }

    // (a)
    core1 = startCore('core1')
    const uiA = waitUi()
    snapshotTree(core1)
    tokenA = uiA?.tokenSha
    if (!uiA) { record('core-start', 'FAIL', { reason: 'ui-reply-timeout', corePid: core1.pid }); blockRest('prerequisite-core-start-failed'); return finish(results, owned, ctx) }
    record('core-start', 'PASS', { corePid: core1.pid, protocol: uiA.reply.protocol, tokenSha: tokenA })

    // (b)
    desktop = remember('desktop', ctx.ps(step.spawnDetached({
      exe: ctx.electron, args: [ctx.app], cwd: ctx.app, stdout: ctx.dirs.winLogs + '\\desktop.out.log', stderr: ctx.dirs.winLogs + '\\desktop.err.log',
      env: [{ name: 'JARVIS_CONTROL_PORT', value: String(ctx.controlPort) }, { name: 'APPDATA', value: ctx.dirs.win + '\\appdata' }],
    })))
    const attached = waitFor(() => (evaluateSequence(desktopMarkers(), 'attach') ? true : null), 60000, ctx)
    snapshotTree(desktop)
    if (!attached) { record('desktop-attach', 'FAIL', { reason: 'connected-marker-timeout', desktopPid: desktop.pid, states: stateSequence(desktopMarkers()) }); blockRest('prerequisite-attach-failed'); return finish(results, owned, ctx) }
    record('desktop-attach', markersLeakSecret(desktopMarkers()) ? 'FAIL' : 'PASS', { desktopPid: desktop.pid, states: stateSequence(desktopMarkers()) })

    // (c) quit goes to the harness Core only.
    const quit = control('quit')
    const lossSeen = waitFor(() => (evaluateSequence(desktopMarkers(), 'loss') ? true : null), 60000, ctx)
    const coreGone = waitFor(() => (asArray(ctx.ps(step.alive([...owned.values()].filter(o => o.role === 'core')))).length === 0 ? true : null), 30000, ctx)
    const desktopAlive = asArray(ctx.ps(step.alive([desktop]))).length === 1
    record('core-quit-desktop-survives', quit.ok && lossSeen && coreGone && desktopAlive ? 'PASS' : 'FAIL', { quitAccepted: quit.ok, lossMarker: Boolean(lossSeen), coreExited: Boolean(coreGone), desktopAlive, states: stateSequence(desktopMarkers()) })

    // (d) A fresh Core generates a fresh token.
    core2 = startCore('core2')
    const uiB = waitUi()
    snapshotTree(core2)
    const reattached = uiB && waitFor(() => (evaluateSequence(desktopMarkers(), 'reattach') ? true : null), 90000, ctx)
    record('core-relaunch-reattach', uiB && reattached && uiB.tokenSha !== tokenA ? 'PASS' : 'FAIL', { corePid: core2?.pid, reattached: Boolean(reattached), tokenRotated: Boolean(uiB && uiB.tokenSha !== tokenA), states: stateSequence(desktopMarkers()) })

    // Lineage while the desktop is still alive: it must own no python process, and Core is not its child.
    snapshotTree(desktop)
    const desktopTree = asArray(ctx.ps(step.tree(desktop)))
    const pythons = desktopTree.filter(p => /^pythonw?\.exe$/i.test(p.name))
    const coreParentIsDesktop = desktopTree.some(p => p.pid === core2.pid)
    record('no-python-spawn', pythons.length === 0 && !coreParentIsDesktop ? 'PASS' : 'FAIL', { desktopDescendants: desktopTree.map(p => p.name), pythonDescendants: pythons.length, coreParentIsDesktop })

    // (e) Terminate the desktop (it exposes no external quit channel); Core must be unaffected.
    const stopDesktop = ctx.ps(step.stop([...owned.values()].filter(o => o.role === 'desktop' || (o.role === 'descendant' && desktopTree.some(p => p.pid === o.pid)))))
    const coreAlive = asArray(ctx.ps(step.alive([core2]))).length === 1
    const status = control('status')
    record('desktop-exit-core-survives', stopDesktop.remaining.length === 0 && coreAlive && status.ok && status.reply?.ok !== false ? 'PASS' : 'FAIL', { desktopRemaining: stopDesktop.remaining.length, coreAlive, statusAnswered: status.ok })

    // (f)
    control('quit')
    waitFor(() => (asArray(ctx.ps(step.alive([...owned.values()]))).length === 0 ? true : null), 30000, ctx)
  } catch (error) {
    record('harness-error', 'FAIL', { reason: String(error.message).slice(0, 300) })
  }
  return finish(results, owned, ctx)
}

// Always stop only identity-matched owned processes, then judge survivors.
function finish(results, owned, ctx) {
  let remaining = null
  // Re-snapshot each root's tree first: an early abort may not have tracked Core's python children yet.
  for (const root of [...owned.values()].filter(o => o.role !== 'descendant')) {
    try { for (const p of asArray(ctx.ps(step.tree(root)))) if (!owned.has(`${p.pid}:${p.ticks}`)) owned.set(`${p.pid}:${p.ticks}`, { role: 'descendant', pid: p.pid, ticks: p.ticks, name: p.name }) } catch { /* stop still runs */ }
  }
  try {
    const stop = ctx.ps(step.stop([...owned.values()]))
    remaining = stop.remaining
  } catch (error) { remaining = [{ error: String(error.message).slice(0, 200) }] }
  const cleanup = results.find(r => r.id === 'core-cleanup')
  if (!cleanup) results.push(scenarioResult('core-cleanup', remaining && remaining.length === 0 && results.some(r => r.id === 'desktop-exit-core-survives') ? 'PASS' : results.some(r => r.id === 'desktop-exit-core-survives') ? 'FAIL' : 'BLOCKED', { remaining: remaining ? remaining.length : null, ownedTracked: owned.size }))
  for (const id of SCENARIOS) if (!results.some(r => r.id === id)) results.push(scenarioResult(id, 'BLOCKED', { reason: 'not-reached' }))
  const ordered = [...SCENARIOS.map(id => results.find(r => r.id === id)), ...results.filter(r => !SCENARIOS.includes(r.id))]
  return { status: aggregate(ordered), scenarios: ordered, survivors: remaining }
}

function main(argv = process.argv.slice(2)) {
  let report
  let args
  try { args = parseArgs(argv) } catch (error) { console.log(JSON.stringify({ status: 'FAIL', reason: error.message })); return 1 }
  const id = Math.random().toString(36).slice(2, 8)
  const localDir = path.join(ROOT, `desktop-native-core-lifecycle-${id}`)
  try {
    const venv = locateVenv(ROOT)
    if (!venv.found) report = blockedReport('windows-venv-missing', { checkedPaths: venv.checked })
    else {
      command('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', 'Write-Output bridge-ready'], 10000)
      const toWindows = p => command('wslpath', ['-w', p], 5000)
      const electron = toWindows(localRuntime(prerequisites(ROOT)))
      fs.mkdirSync(localDir, { mode: 0o700 })
      const temp = command('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', 'Write-Output $env:TEMP'], 10000)
      const winDir = `${temp}\\jarvis-core-lifecycle-${id}`
      const baseConfig = JSON.parse(fs.readFileSync(path.join(ROOT, 'config.win.json'), 'utf8'))
      const derived = deriveConfig(baseConfig, { controlPort: args.controlPort, eventsPort: args.eventsPort, dbPath: winDir + '\\jarvis.db' })
      const configLocal = command('wslpath', ['-u', winDir], 5000)
      fs.mkdirSync(path.join(configLocal, 'data'), { recursive: true })
      fs.writeFileSync(path.join(configLocal, 'config.lifecycle.json'), JSON.stringify(derived.config, null, 2))
      const ctx = {
        ps: powershell, readText: file => fs.readFileSync(file, 'utf8'), ports: [...PROTECTED_PORTS, args.controlPort, args.eventsPort],
        controlPort: args.controlPort, eventsPort: args.eventsPort, python: toWindows(venv.found), src: toWindows(path.join(ROOT, 'src')),
        electron, app: toWindows(path.join(ROOT, 'desktop')), baseConfig,
        dirs: { local: localDir, win: winDir, winLogs: toWindows(localDir) },
      }
      report = runLifecycle(ctx)
      report.config = { source: 'config.win.json', changed: derived.changed, allowedKeys: DERIVED_KEYS, environment: ['LOCALAPPDATA redirected to a harness temp dir for Core', 'APPDATA redirected for the desktop', 'PYTHONPATH=worktree src'], omittedIntegrations: 'none removed from config; audio/STT/TTS run as configured and are not asserted' }
      report.python = venv.found
    }
  } catch (error) { report = { status: 'FAIL', reason: String(error.message).slice(0, 300) } }
  report.generatedAt = new Date().toISOString()
  try {
    const output = args.evidence ? path.resolve(ROOT, args.evidence) : path.join(ROOT, `desktop-native-core-lifecycle-${id}.json`)
    fs.mkdirSync(path.dirname(output), { recursive: true })
    fs.writeFileSync(output, JSON.stringify(report, null, 2))
    report.reportPath = output
  } catch (error) { report.evidenceError = error.message }
  console.log(JSON.stringify(report))
  return exitCodeFor(report.status, process.env.JARVIS_LIFECYCLE_STRICT === '1')
}
if (require.main === module) process.exitCode = main()
module.exports = { SCENARIOS, PROTECTED_PORTS, DERIVED_KEYS, parsePort, parseArgs, deriveConfig, venvCandidates, locateVenv, parseCoreMarkers, markersLeakSecret, stateSequence, evaluateSequence, scenarioResult, aggregate, blockedReport, exitCodeFor, waitFor, step, runLifecycle, main }
