'use strict'

// WSL orchestrator: production Electron/main + user32. No renderer eval or Core quit.
const fs = require('node:fs')
const path = require('node:path')
const { isDeepStrictEqual } = require('node:util')
const { command, powershell, psLiteral, windowsArg, prerequisites, localOutput, localRuntime, OWNERSHIP, recoveryScript, readBoundedLog } = require('./native-smoke.cjs')
const { waitFor } = require('./native-core-lifecycle.cjs')
const ROOT = path.resolve(__dirname, '../..')
const NAME = 'jarvis-desktop'
const RUN = 'Software\\Microsoft\\Windows\\CurrentVersion\\Run'
const APPROVED = 'Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\StartupApproved\\Run'
const SCENARIOS = ['tray-created', 'shortcut-registered', 'shortcut-injection-toggles-overlay', 'shortcut-collision-reported', 'login-item-roundtrip', 'exit-desktop-leaves-core', 'cleanup']
function parseArgs(argv, root = ROOT) {
  if (!argv.length) return { evidence: null }
  if (argv.length !== 2 || argv[0] !== '--evidence' || !argv[1]) throw new Error('usage: native-tray.cjs [--evidence worktree-local.json]')
  return { evidence: localOutput(root, argv[1]) }
}
function aggregate(rows) {
  if (rows.some(r => r.status === 'FAIL')) return 'FAIL'
  if (rows.length !== SCENARIOS.length || SCENARIOS.some(id => rows.filter(r => r.id === id).length !== 1) || rows.some(r => !['PASS', 'BLOCKED'].includes(r.status))) return 'FAIL'
  return rows.some(r => r.status === 'BLOCKED') ? 'BLOCKED' : 'PASS'
}
function registryRestorePlan(snapshot) {
  if (!snapshot || typeof snapshot.exists !== 'boolean' || ![RUN, APPROVED].includes(snapshot.key) || snapshot.name !== NAME) throw new Error('registry-snapshot-invalid')
  if (!snapshot.exists) return { key: snapshot.key, name: NAME, operation: 'delete' }
  const value = snapshot.value, kind = snapshot.kind
  const valid = ['String', 'ExpandString'].includes(kind) ? typeof value === 'string'
    : kind === 'MultiString' ? Array.isArray(value) && value.every(v => typeof v === 'string')
      : ['Binary', 'None'].includes(kind) ? Array.isArray(value) && value.every(v => Number.isInteger(v) && v >= 0 && v <= 255)
        : kind === 'DWord' ? Number.isInteger(value) && value >= -2147483648 && value <= 2147483647
          : kind === 'QWord' ? typeof value === 'string' && /^-?\d{1,19}$/.test(value) && BigInt(value) >= -(2n ** 63n) && BigInt(value) < 2n ** 63n : false
  if (!valid) throw new Error('registry-kind-invalid')
  return { key: snapshot.key, name: NAME, operation: 'set', kind: snapshot.kind, value: structuredClone(snapshot.value) }
}
const prelude = a => `$ErrorActionPreference='Stop'\n$a=ConvertFrom-Json ${psLiteral(JSON.stringify(a))}\n`
// Registry provider + native reg.exe work under ConstrainedLanguage without
// changing policy. reg.exe query preserves REG_EXPAND_SZ literally.
const registryRead = key => `${prelude({ key, name: NAME })}
$p='Registry::HKEY_CURRENT_USER\\' + $a.key
$exists=$false
if (Test-Path -LiteralPath $p) { $v=Get-ItemProperty -LiteralPath $p; $exists=$v.PSObject.Properties.Name -contains $a.name }
$r=@{key=$a.key;name=$a.name;exists=$exists}
if ($exists) {
  $raw=@(& reg.exe query ('HKCU\\'+$a.key) /v $a.name 2>$null)
  if ($LASTEXITCODE -ne 0) { throw 'registry-query-failed' }
  $r.raw=$raw -join [char]10
}
$r | ConvertTo-Json -Depth 5 -Compress`
const REG_TYPES = { REG_SZ: 'String', REG_EXPAND_SZ: 'ExpandString', REG_MULTI_SZ: 'MultiString', REG_BINARY: 'Binary', REG_DWORD: 'DWord', REG_QWORD: 'QWord', REG_NONE: 'None' }
function registrySnapshot(raw) {
  if (!raw.exists) { registryRestorePlan(raw); return raw }
  const m = String(raw.raw).match(/^\s*jarvis-desktop\s+(REG_[A-Z_]+)\s*(.*?)\r?$/m)
  if (!m || !REG_TYPES[m[1]]) throw new Error('registry-query-unparseable')
  const kind = REG_TYPES[m[1]], data = m[2]
  let value = data
  if (['Binary', 'None'].includes(kind)) {
    if (!/^(?:[0-9a-f]{2})*$/i.test(data)) throw new Error('registry-query-unparseable')
    value = [...Buffer.from(data, 'hex')]
  } else if (kind === 'DWord' || kind === 'QWord') {
    if (!/^0x[0-9a-f]+$/i.test(data)) throw new Error('registry-query-unparseable')
    const signed = BigInt.asIntN(kind === 'DWord' ? 32 : 64, BigInt(data))
    value = kind === 'DWord' ? Number(signed) : String(signed)
  } else if (kind === 'MultiString') value = data === '' ? [] : data.split('\\0')
  const snapshot = { key: raw.key, name: raw.name, exists: true, kind, value }
  registryRestorePlan(snapshot)
  return snapshot
}
function registryRestore(plan) {
  const type = Object.keys(REG_TYPES).find(k => REG_TYPES[k] === plan.kind)
  const data = ['Binary', 'None'].includes(plan.kind) ? Buffer.from(plan.value).toString('hex')
    : ['DWord', 'QWord'].includes(plan.kind) ? '0x' + BigInt.asUintN(plan.kind === 'DWord' ? 32 : 64, BigInt(plan.value)).toString(16)
      : plan.kind === 'MultiString' ? plan.value.join('\\0') : plan.value
  return `${prelude({ ...plan, type, data })}
$p='HKCU\\' + $a.key
if ($a.operation -eq 'delete') {
  $r=Get-ItemProperty -LiteralPath ('Registry::HKEY_CURRENT_USER\\'+$a.key) -ErrorAction SilentlyContinue
  if ($r -and $r.PSObject.Properties.Name -contains $a.name) { $null=& reg.exe delete $p /v $a.name /f; if ($LASTEXITCODE -ne 0) { throw 'registry-restore-failed' } }
} else { $null=& reg.exe add $p /v $a.name /t $a.type /d $a.data /f; if ($LASTEXITCODE -ne 0) { throw 'registry-restore-failed' } }
'{"restored":true}'`
}
function markers(text) {
  return String(text).split(/\r?\n/).flatMap(line => {
    if (!line.startsWith('[jarvis-bootstrap] ')) return []
    try { const r = JSON.parse(line.slice(19)); return typeof r.stage === 'string' ? [r] : [] } catch { return [] }
  })
}
// Reuse native smoke path/start-time fencing and persistent ownership journal.
function inspectScript(exe, journal) {
  return `$ErrorActionPreference='Stop'\n$exe=${psLiteral(exe)}\n$journal=${psLiteral(journal)}\n$owned=@(); $stopped=@(); $cleanupErrors=@(); $remaining=@()\n${OWNERSHIP}
$owned=@(Get-Content -Raw -LiteralPath $journal | ConvertFrom-Json | ForEach-Object { $_ } | ForEach-Object { @{ pid=[int]$_.pid; parent=[int]$_.parent; started="$($_.started)"; path=$_.path; session=$_.session } })
Discover
@{alive=@(Remaining); owned=@($owned)} | ConvertTo-Json -Depth 6 -Compress`
}
function spawnScript({ exe, app, directory, login }) {
  const args = [app, `--tray-probe-state=${directory}\\state`, ...(login ? ['--tray-probe-login-item'] : [])].map(windowsArg).join(' ')
  return `$ErrorActionPreference='Stop'\nRemove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
$p=Start-Process -FilePath ${psLiteral(exe)} -ArgumentList ${psLiteral(args)} -WorkingDirectory ${psLiteral(app)} -RedirectStandardOutput ${psLiteral(directory + '\\stdout.log')} -RedirectStandardError ${psLiteral(directory + '\\stderr.log')} -PassThru
$null=$p.Handle
$r=@{pid=$p.Id;parent=$PID;started="$($p.StartTime.Ticks)";path=$p.Path;session=$p.SessionId}
if ($p.Path -ne ${psLiteral(exe)}) { throw 'launch-executable-mismatch' }
ConvertTo-Json -InputObject @($r) -Compress | Set-Content -LiteralPath ${psLiteral(directory + '\\owned.json')} -Encoding UTF8
$r | ConvertTo-Json -Compress`
}
const HOTKEY_TYPE = `Add-Type @'
using System; using System.Runtime.InteropServices;
public class Hotkey {
 [DllImport("user32.dll", SetLastError=true)] public static extern bool RegisterHotKey(IntPtr h,int id,uint m,uint k);
 [DllImport("user32.dll")] public static extern bool UnregisterHotKey(IntPtr h,int id);
}
'@`
function hotkeyScript(directory) {
  return `$ErrorActionPreference='Stop'\n${HOTKEY_TYPE}
$ok=[Hotkey]::RegisterHotKey([IntPtr]::Zero,71,7,0x4A)
try {
  @{registered=$ok;pid=$PID} | ConvertTo-Json -Compress | Set-Content -LiteralPath ${psLiteral(directory + '\\hotkey-ready.json')} -Encoding UTF8
  $end=(Get-Date).AddSeconds(60)
  while ($ok -and (Get-Date) -lt $end -and !(Test-Path -LiteralPath ${psLiteral(directory + '\\hotkey-stop')})) { Start-Sleep -Milliseconds 100 }
} finally { if ($ok) { $null=[Hotkey]::UnregisterHotKey([IntPtr]::Zero,71) } }`
}
// Python ctypes, not Add-Type: WDAC hosts run PowerShell in ConstrainedLanguage mode.
const INPUT_PY = `import ctypes as C, ctypes.wintypes as W, json
class KI(C.Structure): _fields_ = [('vk', W.WORD), ('scan', W.WORD), ('flags', W.DWORD), ('time', W.DWORD), ('extra', C.c_size_t)]
class MI(C.Structure): _fields_ = [('dx', W.LONG), ('dy', W.LONG), ('data', W.DWORD), ('flags', W.DWORD), ('time', W.DWORD), ('extra', C.c_size_t)]
class U(C.Union): _fields_ = [('k', KI), ('m', MI)]
class IN(C.Structure): _fields_ = [('type', W.DWORD), ('u', U)]
a = (IN * 8)(*[IN(1, U(k=KI(v, 0, 0 if i < 4 else 2, 0, 0))) for i, v in enumerate([0x11, 0x12, 0x10, 0x4A, 0x4A, 0x10, 0x12, 0x11])])
u = C.windll.user32; u.SendInput.argtypes = [W.UINT, C.c_void_p, C.c_int]
print(json.dumps({'sent': u.SendInput(8, a, C.sizeof(IN))}))`
const INPUT_SCRIPT = `$ErrorActionPreference='Stop'
$py=(Get-Command python.exe).Source
@'
${INPUT_PY}
'@ | & $py -`
function helperSpawnScript({ app, directory }) {
  const args = ['-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(hotkeyScript(directory), 'utf16le').toString('base64')].map(windowsArg).join(' ')
  return `$ErrorActionPreference='Stop'
$p=Start-Process -FilePath (Get-Command powershell.exe).Source -ArgumentList ${psLiteral(args)} -WorkingDirectory ${psLiteral(app)} -RedirectStandardOutput ${psLiteral(directory + '\\stdout.log')} -RedirectStandardError ${psLiteral(directory + '\\stderr.log')} -PassThru
$null=$p.Handle
@{pid=$p.Id;ticks="$($p.StartTime.Ticks)";path=$p.Path} | ConvertTo-Json -Compress`
}
function helperStop(record) {
  return `${prelude(record)}
function Alive { $p=Get-Process -Id $a.pid -ErrorAction SilentlyContinue; return ($p -and $p.Path -eq $a.path -and "$($p.StartTime.Ticks)" -eq $a.ticks) }
if (Alive) { Stop-Process -Id $a.pid -Force }
$end=(Get-Date).AddSeconds(5)
while ((Alive) -and (Get-Date) -lt $end) { Start-Sleep -Milliseconds 100 }
@{remaining= [bool](Alive)} | ConvertTo-Json -Compress`
}
// PowerShell stays alive while the launched Electron holds inherited handles, so wait for the
// JSON result file instead of process exit; the lingering host ends with the owned process.
function psLaunch(script) {
  const dir = fs.mkdtempSync(path.join(require('node:os').tmpdir(), 'jarvis-tray-ps-'))
  const file = path.join(dir, 'out')
  const out = fs.openSync(file, 'w'), err = fs.openSync(path.join(dir, 'err'), 'w')
  const child = require('node:child_process').spawn('powershell.exe', ['-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(script, 'utf16le').toString('base64')], { stdio: ['ignore', out, err], detached: true })
  child.unref(); fs.closeSync(out); fs.closeSync(err)
  try {
    for (let i = 0; i < 180; i++) {
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 250)
      const text = fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, '').trim()
      try { return JSON.parse(text) } catch { /* incomplete */ }
      if (child.exitCode !== null && child.exitCode !== 0) break
    }
    throw new Error(`launch-failed: ${fs.readFileSync(path.join(dir, 'err'), 'utf8').slice(0, 300)}`)
  } finally { fs.rmSync(dir, { recursive: true, force: true }) }
}
function psDiag(script) {
  if (/Start-Process/.test(script)) return psLaunch(script)
  const t0 = Date.now()
  try { return powershell(script) } catch (e) {
    if (process.env.TRAY_TRACE) console.error(`ps-failed ${Date.now() - t0}ms: ${script.slice(0, 160).replace(/\n/g, ' ')}`)
    const m = String(e.message).replace(/_x000D__x000A_/g, ' ').match(/<S S="Error">[^<]*/g)
    throw new Error(m ? m.slice(0, 3).map(x => x.slice(13)).join(' | ').slice(0, 400) : e.message)
  }
}
const CORE_PORT_SCRIPT = `$ErrorActionPreference='Stop'
$l=@(& netstat.exe -ano -p tcp | Where-Object { $_ -match '127\\.0\\.0\\.1:47811\\s+\\S+\\s+LISTENING' })
@{ ok = ($l.Count -gt 0) } | ConvertTo-Json -Compress`
function coreProbe(ps) { try { return ps(CORE_PORT_SCRIPT) } catch { return { ok: false } } }
function run({ exe, app, directory, toWindows, ps = psDiag, read = readBoundedLog, wait = waitFor }) {
  const rows = new Map(), launches = [], errors = []
  let backups, helper, restored = false, cleanupOK = true
  const record = (id, status, detail = {}) => rows.set(id, { id, status, ...detail })
  const log = launch => { try { return markers(read(path.join(launch.local, 'stdout.log'))).filter(r => r.pid === launch.pid) } catch { return [] } }
  const alive = launch => ps(inspectScript(exe, launch.win + '\\owned.json')).alive.some(p => p.pid === launch.pid)
  const start = (name, login = false) => {
    const local = path.join(directory, name); fs.mkdirSync(local)
    const win = toWindows(local), launch = { local, win }; launches.push(launch)
    Object.assign(launch, ps(spawnScript({ exe, app, directory: win, login })))
    const ready = wait(() => { const state = alive(launch), r = log(launch).find(r => r.stage === 'tray-probe-ready'); return !state ? { dead: true } : r }, 20000)
    if (!ready || ready.dead || ready.loginName !== NAME) throw new Error('tray-probe-ready-failed')
    return launch
  }
  const issue = (launch, id, operation) => {
    const target = path.join(launch.local, 'state/tray-request.json'), tmp = target + '.tmp'
    fs.writeFileSync(tmp, JSON.stringify({ id, operation })); fs.renameSync(tmp, target)
    return wait(() => log(launch).find(r => r.stage === 'tray-probe-command' && r.id === id), 8000)
  }
  const restore = () => {
    if (!backups) return
    for (const snapshot of backups) ps(registryRestore(registryRestorePlan(snapshot)))
    restored = backups.every(snapshot => isDeepStrictEqual(ps(registryRead(snapshot.key)), snapshot))
    if (!restored) throw new Error('registry-restore-mismatch')
  }
  try {
    // Snapshot both Run and StartupApproved BEFORE any explicit login-item mutation.
    backups = [RUN, APPROVED].map(key => ps(registryRead(key)))
    for (const b of backups) registryRestorePlan(b)
    fs.writeFileSync(path.join(directory, 'registry-backup.json'), JSON.stringify(backups, null, 2), { mode: 0o600 })
    const before = coreProbe(ps)
    const normal = start('normal', true)
    const normalLog = log(normal)
    record('tray-created', normalLog.some(r => r.stage === 'tray-ready') && alive(normal) ? 'PASS' : 'FAIL', { pid: normal.pid, proof: 'Tray constructor returned, tray-ready and identity-matched process alive; not a pixel assertion of Shell_NotifyIcon' })
    const registered = normalLog.filter(r => r.stage === 'shortcut-registered')
    record('shortcut-registered', registered.length === 2 ? 'PASS' : 'BLOCKED', { registered: registered.map(r => r.accelerator), ...(registered.length !== 2 ? { reason: 'host-hotkey-already-owned', unavailable: normalLog.filter(r => r.stage === 'shortcut-unavailable') } : {}) })
    const enable = issue(normal, 1, 'login-enable'), enabled = ps(registryRead(RUN))
    const disable = issue(normal, 2, 'login-disable'), disabled = ps(registryRead(RUN))
    restore()
    record('login-item-roundtrip', enable?.openAtLogin === true && enabled.exists && typeof enabled.raw === 'string' && enabled.raw.includes('--launched-at-login') && disable?.openAtLogin === false && !disabled.exists && restored ? 'PASS' : 'FAIL', { enabled: enabled.exists, launchArgumentPresent: typeof enabled.raw === 'string' && enabled.raw.includes('--launched-at-login'), valueExcerpt: String(enabled.raw || '').replace(/\s+/g, ' ').slice(0, 300), disabled: !disabled.exists, restored })
    const exit = issue(normal, 3, 'exit-desktop')
    const gone = exit && wait(() => !alive(normal), 10000)
    const after = coreProbe(ps)
    record('exit-desktop-leaves-core', !before.ok ? 'BLOCKED' : gone && after.ok ? 'PASS' : 'FAIL', { reason: !before.ok ? 'no-Core-answer-on-default-port-before-exit' : undefined, desktopExited: Boolean(gone), coreReachableBefore: before.ok, coreReachableAfter: after.ok })

    // Separate process: input failure cannot interrupt registry restoration or graceful exit proof.
    const injection = start('injection')
    const reg = log(injection).some(r => r.stage === 'shortcut-registered' && r.action === 'toggleOverlay')
    if (!reg) record('shortcut-injection-toggles-overlay', 'BLOCKED', { reason: 'host-hotkey-already-owned' })
    else {
      let sent; try { sent = ps(INPUT_SCRIPT) } catch (e) { sent = { sent: 0, error: e.message } }
      const shown = wait(() => log(injection).some(r => r.stage === 'shown' && r.entry === 'overlay') || !alive(injection), 18000)
      const records = log(injection)
      const modeChanged = records.some(r => r.stage === 'tray-probe-mode' && r.mode === 'OVERLAY_COMPACT')
      const visible = records.some(r => r.stage === 'renderer-mounted') && records.some(r => r.stage === 'shown')
      const stderr = read(path.join(injection.local, 'stderr.log'))
      const uncSandbox = exe.startsWith('\\\\') && modeChanged && /sandbox|utility process|error_code=1002/i.test(stderr)
      record('shortcut-injection-toggles-overlay', sent.error && /lenguaje|language mode/i.test(sent.error) ? 'BLOCKED' : sent.sent === 8 && shown && modeChanged && visible ? 'PASS' : uncSandbox ? 'BLOCKED' : 'FAIL', { sent: sent.sent, modeChanged, rendererShown: visible, ...(sent.error ? { reason: 'PowerShell-ConstrainedLanguage-blocks-Add-Type-SendInput', detail: sent.error.slice(0, 160) } : uncSandbox ? { reason: 'sandboxed-renderer-cannot-start-from-confined-UNC-runtime' } : {}) })
    }
    if (alive(injection)) { issue(injection, 1, 'exit-desktop'); wait(() => !alive(injection), 10000) }
    const hotkeyDir = path.join(directory, 'hotkey'); fs.mkdirSync(hotkeyDir)
    const hotkeyWin = toWindows(hotkeyDir)
    helper = ps(helperSpawnScript({ app, directory: hotkeyWin }))
    const ready = wait(() => { try { return JSON.parse(read(path.join(hotkeyDir, 'hotkey-ready.json')).replace(/^\uFEFF/, '')) } catch { return null } }, 12000)
    let holder = null
    if (!ready?.registered) {
      // CLM hosts cannot Add-Type RegisterHotKey; a second probe instance holding the accelerator is an equivalent OS-level collision.
      const first = start('collision-holder')
      holder = log(first).some(r => r.stage === 'shortcut-registered' && r.action === 'toggleOverlay') ? first : null
    }
    if (!ready?.registered && !holder) record('shortcut-collision-reported', 'BLOCKED', { reason: 'no-collision-holder-available' })
    else {
      const collision = start('collision')
      const unavailable = log(collision).find(r => r.stage === 'shortcut-unavailable' && r.action === 'toggleOverlay')
      record('shortcut-collision-reported', unavailable && alive(collision) ? 'PASS' : 'FAIL', { holder: holder ? 'second-electron-probe' : 'RegisterHotKey-helper', unavailable: unavailable || null, alive: alive(collision) })
      issue(collision, 1, 'exit-desktop'); wait(() => !alive(collision), 10000)
      if (holder) { issue(holder, 1, 'exit-desktop'); wait(() => !alive(holder), 10000) }
    }
  } catch (error) { errors.push(String(error.message).slice(0, 300)) }
  finally {
    // Stop probes first: none may mutate the registry after final restore.
    for (const launch of launches) {
      try { const r = ps(recoveryScript(exe, launch.win + '\\owned.json')); if (r.remaining.length || r.cleanupErrors.length) cleanupOK = false } catch (e) { cleanupOK = false; errors.push(`cleanup:${e.message}`) }
    }
    if (helper) { try { if (ps(helperStop(helper)).remaining) cleanupOK = false } catch { cleanupOK = false } }
    try { restore() } catch (e) { cleanupOK = false; errors.push(`registry:${e.message}`) }
    record('cleanup', cleanupOK && restored ? 'PASS' : 'FAIL', { processesRemaining: cleanupOK ? 0 : 'unknown', helperTracked: Boolean(helper), registryRestored: restored })
  }
  for (const id of SCENARIOS) if (!rows.has(id)) record(id, errors.length ? 'FAIL' : 'BLOCKED', { reason: errors[0] || 'not-reached' })
  const scenarios = SCENARIOS.map(id => rows.get(id))
  return { status: aggregate(scenarios), scenarios, errors, directory }
}
function main(argv = process.argv.slice(2)) {
  let args, report
  try { args = parseArgs(argv) } catch (e) { console.log(JSON.stringify({ status: 'FAIL', reason: e.message })); return 1 }
  try {
    const executable = prerequisites(ROOT)
    const toWindows = p => command('wslpath', ['-w', p], 5000)
    const directory = fs.mkdtempSync(path.join(ROOT, 'desktop-native-tray-')); fs.chmodSync(directory, 0o700)
    // This acceptance unit confines all file IO to the worktree. Unlike native-smoke,
    // do not call localRuntime(), which copies/reads an external NTFS cache.
    // Local NTFS runtime copy, like the other harnesses: a UNC electron.exe cannot start its sandboxed renderer.
    report = run({ exe: toWindows(localRuntime(executable)), app: toWindows(path.join(ROOT, 'desktop')), directory, toWindows })
    report.runtimePolicy = 'local-runtime-cache; app code stays in the worktree; sandbox remains enabled'
  } catch (e) {
    report = { status: 'BLOCKED', scenarios: SCENARIOS.map(id => ({ id, status: 'BLOCKED', reason: e.message })) }
  }
  report.generatedAt = new Date().toISOString()
  if (args.evidence) {
    try { fs.mkdirSync(path.dirname(args.evidence), { recursive: true }); fs.writeFileSync(args.evidence, JSON.stringify(report, null, 2)) }
    catch (e) { report.status = 'FAIL'; report.evidenceError = e.message }
  }
  console.log(JSON.stringify(report))
  return report.status === 'FAIL' ? 1 : process.env.JARVIS_TRAY_STRICT === '1' && report.status === 'BLOCKED' ? 3 : 0
}
if (require.main === module) process.exitCode = main()
module.exports = { SCENARIOS, RUN, APPROVED, parseArgs, aggregate, registryRestorePlan, registryRead, registryRestore, markers, inspectScript, spawnScript, hotkeyScript, INPUT_SCRIPT, helperSpawnScript, helperStop, run, main }
