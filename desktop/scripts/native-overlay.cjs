'use strict'

// Explicit CLI only. No native launch, server or filesystem effect on import.
const fs = require('node:fs')
const path = require('node:path')
const smoke = require('./native-smoke.cjs')
const ROOT = path.resolve(__dirname, '../..')
const APPS = ['explorer', 'vscode', 'browser', 'terminal']
const ROUTES = ['empty-root', 'transparent-margin', 'exit-reentry', 'leave-reset', 'hide-reset']
const REQUIRED = [...APPS.flatMap(app => ROUTES.map(route => `${app}.${route}`)), ...['control', 'mode-roundtrip', 'drag', 'native-surface', 'compositor'].map(id => `overlay.${id}`)]
const RECEIPTS = ['controlled-page-pointer', 'uia-focus']
function identity(p) { return p && Number.isSafeInteger(p.pid) && p.pid > 0 && typeof p.started === 'string' && /^\d+$/.test(p.started) && typeof p.path === 'string' && p.path.length > 0 }
function sameIdentity(a, b) { return Boolean(identity(a) && identity(b) && a.pid === b.pid && a.started === b.started && a.path === b.path) }
function coordinate(input) {
  const r = input?.clientBounds, p = input?.client, s = input?.screen
  if (!r || !p || !s || ![r.x, r.y, r.width, r.height, p.x, p.y, s.x, s.y].every(Number.isSafeInteger) || r.width <= 0 || r.height <= 0 || p.x < r.x || p.y < r.y || p.x >= r.x + r.width || p.y >= r.y + r.height) throw new Error('off-target-or-malformed-coordinates')
  if (!sameIdentity(input.target, input.revalidated) || !/^\d+$/.test(input.hwnd) || BigInt(input.hwnd) === 0n || input.foreground !== input.hwnd || input.visible !== true || input.hitValidated !== true) throw new Error('input-identity-or-focus-denied')
}
function validate(report, fileExists = () => false) {
  if (!report || report.platform !== 'win32' || report.realInput !== true || report.preflight?.ctypes !== true || !Array.isArray(report.scenarios) || report.scenarios.length !== REQUIRED.length || new Set(report.scenarios.map(s => s.id)).size !== REQUIRED.length) throw new Error('incomplete-native-report')
  if (!report.cleanup || !Array.isArray(report.cleanup.remaining) || !Array.isArray(report.cleanup.errors) || report.cleanup.remaining.length || report.cleanup.errors.length) throw new Error('cleanup-failed')
  for (const id of REQUIRED) {
    const s = report.scenarios.find(row => row.id === id)
    if (!s || !['PASS', 'FAIL', 'BLOCKED'].includes(s.status)) throw new Error('missing-or-invalid-scenario')
    if (s.status !== 'PASS') { if (typeof s.reason !== 'string' || !s.reason.length) throw new Error('missing-gap-reason'); continue }
    if (!identity(s.target) || !/^\d+$/.test(s.hwnd) || BigInt(s.hwnd) === 0n || !Array.isArray(s.inputs) || s.inputs.length > 32 || !Array.isArray(s.screenshots) || !s.screenshots.length || !s.screenshots.every(f => typeof f === 'string' && fileExists(f))) throw new Error('incomplete-pass-evidence')
    s.inputs.forEach(input => { coordinate(input); if (!sameIdentity(s.target, input.target) || s.hwnd !== input.hwnd) throw new Error('scenario-input-identity-mismatch') })
    if (!id.startsWith('overlay.')) {
      if (!s.inputs.some(input => Array.isArray(input.flags) && input.flags.includes(2)) || !s.receipt || !RECEIPTS.includes(s.receipt.kind) || !Number.isSafeInteger(s.receipt.before) || !Number.isSafeInteger(s.receipt.after) || s.receipt.after <= s.receipt.before || s.receipt.correlated !== true) throw new Error('independent-recipient-observation-required')
    } else if (id === 'overlay.control' || id === 'overlay.mode-roundtrip') {
      if (!s.inputs.length || s.observation?.rendererNativeAgree !== true || s.observation?.roundtrip !== true) throw new Error('mode-response-required')
    } else if (id === 'overlay.drag') {
      if (s.inputs.length < 2 || !Number.isSafeInteger(s.observation?.dx) || !Number.isSafeInteger(s.observation?.dy) || Math.abs(s.observation.dx) + Math.abs(s.observation.dy) < 8) throw new Error('native-displacement-required')
    } else if (id === 'overlay.native-surface') {
      if (s.observation?.borderless !== true || s.observation?.topmost !== true || s.observation?.aboveRecipient !== true) throw new Error('native-styles-required')
    } else if (id === 'overlay.compositor') {
      if (s.screenshots.length < 2 || s.observation?.marginEqual !== true || s.observation?.cardDifferent !== true) throw new Error('compositor-observation-required')
    }
  }
  return report.scenarios.every(s => s.status === 'PASS') ? 'PASS' : report.scenarios.some(s => s.status === 'FAIL') ? 'FAIL' : 'BLOCKED'
}

function bridgeScript({ exe, app, root, directory }) {
  const state = directory + '\\state', journal = directory + '\\owned.json'
  const args = [app, `--overlay-probe-state=${state}`].map(smoke.windowsArg).join(' ')
  const pythonArgs = [root + '\\desktop\\scripts\\windows-overlay-probe.py', '--root', root, '--directory', directory].map(smoke.windowsArg).join(' ')
  return `$ErrorActionPreference = 'Stop'
$exe = ${smoke.psLiteral(exe)}
$journal = ${smoke.psLiteral(journal)}
$owned=@(); $remaining=@(); $cleanupErrors=@(); $stopped=@(); $probeOwned=@()
$errorReason=$null; $electronExit=$null; $pythonExit=$null
${smoke.OWNERSHIP}
try {
  $python = (Get-Command python.exe -ErrorAction Stop).Source
  # Verify actual execution/ctypes before launching any scenario.
  $preflight = & $python ${smoke.psLiteral(root + '\\desktop\\scripts\\windows-overlay-probe.py')} --preflight
  if ($LASTEXITCODE -ne 0) { throw 'python-ctypes-preflight-failed' }
  $apps = @{
    explorer = (Join-Path $env:WINDIR 'explorer.exe')
    terminal = (Join-Path $env:WINDIR 'System32\\cmd.exe')
    browser = $null; vscode = $null
  }
  foreach ($candidate in @((Join-Path \${env:ProgramFiles(x86)} 'Microsoft\\Edge\\Application\\msedge.exe'), (Join-Path $env:ProgramFiles 'Google\\Chrome\\Application\\chrome.exe'))) {
    if (!$apps.browser -and (Test-Path -LiteralPath $candidate)) { $apps.browser=$candidate }
  }
  foreach ($candidate in @((Join-Path $env:LOCALAPPDATA 'Programs\\Microsoft VS Code\\Code.exe'), (Join-Path $env:ProgramFiles 'Microsoft VS Code\\Code.exe'))) {
    if (!$apps.vscode -and (Test-Path -LiteralPath $candidate)) { $apps.vscode=$candidate }
  }
  $apps | ConvertTo-Json -Compress | Set-Content -LiteralPath ${smoke.psLiteral(directory + '\\apps.json')} -Encoding UTF8
  Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
  $electron = Start-Process -FilePath $exe -ArgumentList ${smoke.psLiteral(args)} -WorkingDirectory ${smoke.psLiteral(app)} -RedirectStandardOutput ${smoke.psLiteral(directory + '\\stdout.log')} -RedirectStandardError ${smoke.psLiteral(directory + '\\stderr.log')} -PassThru
  $null=$electron.Handle
  # The main module can be unreadable for a moment after launch; bounded wait, then compare.
  for ($i=0; $i -lt 40 -and (Get-Process -Id $electron.Id -ErrorAction SilentlyContinue).Path -ne $exe; $i++) { Start-Sleep -Milliseconds 50 }
  $electronPath = (Get-Process -Id $electron.Id -ErrorAction SilentlyContinue).Path
  if ($electronPath -ne $exe) { throw 'electron-path-mismatch' }
  $owned += @{pid=$electron.Id;parent=$PID;started="$($electron.StartTime.ToUniversalTime().Ticks)";path=$electronPath;session=$electron.SessionId}
  $owned | ConvertTo-Json -Compress | Set-Content -LiteralPath $journal -Encoding UTF8
  $probe = Start-Process -FilePath $python -ArgumentList ${smoke.psLiteral(pythonArgs)} -WorkingDirectory ${smoke.psLiteral(root)} -RedirectStandardOutput ${smoke.psLiteral(directory + '\\probe.stdout.log')} -RedirectStandardError ${smoke.psLiteral(directory + '\\probe.stderr.log')} -PassThru
  $null=$probe.Handle
  $probeOwned=@{pid=$probe.Id;path=$probe.Path;started="$($probe.StartTime.ToUniversalTime().Ticks)"}
  $probeOwned | ConvertTo-Json -Compress | Set-Content -LiteralPath ${smoke.psLiteral(directory + '\\probe-owned.json')} -Encoding UTF8
  $end=(Get-Date).AddSeconds(105)
  do {
    Discover
    if ($probe.HasExited) { $pythonExit=$probe.ExitCode; break }
    if ($electron.HasExited) { throw 'overlay-exited-before-probe' }
    Start-Sleep -Milliseconds 100
  } while ((Get-Date) -lt $end)
  if (!$probe.HasExited) { throw 'native-overlay-timeout' }
  $end=(Get-Date).AddSeconds(5)
  while (!$electron.HasExited -and (Get-Date) -lt $end) { Discover; Start-Sleep -Milliseconds 100 }
  if (!$electron.HasExited) { throw 'overlay-quit-timeout' }
  $electronExit=$electron.ExitCode
} catch { $errorReason=$_.Exception.Message }
finally {
  if ($probeOwned.Count) {
    $live=Get-Process -Id $probeOwned.pid -ErrorAction SilentlyContinue
    if ($live -and $live.Path -eq $probeOwned.path -and "$($live.StartTime.ToUniversalTime().Ticks)" -eq $probeOwned.started) {
      try { Stop-Process -InputObject $live -Force -ErrorAction Stop } catch { $cleanupErrors+='probe-stop-denied' }
    }
  }
  try { Discover } catch { $cleanupErrors+='cleanup-inventory-failed' }
  Cleanup
}
@{error=$errorReason;electronExit=$electronExit;pythonExit=$pythonExit;remaining=@($remaining);cleanupErrors=@($cleanupErrors);owned=@($owned)} | ConvertTo-Json -Depth 6 -Compress
`
}

// Emergency recipient/probe cleanup uses handles/start ticks recorded by Python.
// Never trusts a PID alone and never kills image-wide or discovers arbitrary children.
function recoveryScript(config) {
  return smoke.recoveryScript(config.exe, config.directory + '\\owned.json').replace(/@\{ remaining=.*\| ConvertTo-Json -Depth 5 -Compress\n$/, '') + `
foreach ($name in @('recipients.json','probe-owned.json')) {
  $file=Join-Path ${smoke.psLiteral(config.directory)} $name
  if (Test-Path -LiteralPath $file) {
    $decoded=Get-Content -Raw -LiteralPath $file | ConvertFrom-Json
    $records=@($decoded)
    if ($records.Count -gt 512) { $cleanupErrors+='recipient-budget'; continue }
    foreach ($r in $records) {
      $recipientLive=Get-Process -Id $r.pid -ErrorAction SilentlyContinue
      if ($recipientLive -and $recipientLive.Path -eq $r.path -and "$($recipientLive.StartTime.ToUniversalTime().Ticks)" -eq $r.started) {
        try { Stop-Process -InputObject $recipientLive -Force -ErrorAction Stop } catch { $cleanupErrors+='recipient-stop-denied' }
      }
    }
    foreach ($r in $records) {
      $recipientLive=Get-Process -Id $r.pid -ErrorAction SilentlyContinue
      if ($recipientLive -and $recipientLive.Path -eq $r.path -and "$($recipientLive.StartTime.ToUniversalTime().Ticks)" -eq $r.started) { $remaining+=$r }
    }
  } else { $cleanupErrors+='missing-'+$name }
}
@{remaining=@($remaining);cleanupErrors=@($cleanupErrors);stopped=@($stopped)} | ConvertTo-Json -Depth 6 -Compress
`
}
function nativeBridge(script) {
  return JSON.parse(smoke.command('powershell.exe', ['-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(script, 'utf16le').toString('base64')], 130000))
}
function run({ config, directory, bridge = nativeBridge, readReport = p => JSON.parse(smoke.readBoundedLog(p)), exists = f => {
  const file = smoke.localOutput(ROOT, f)
  if (path.dirname(file) !== directory || !fs.statSync(file).isFile() || !/^screenshot-\d{2}\.bmp$/.test(path.basename(file))) return false
  const fd = fs.openSync(file, 'r'), header = Buffer.alloc(2)
  try { fs.readSync(fd, header, 0, 2, 0); return header.toString() === 'BM' } finally { fs.closeSync(fd) }
} }) {
  const evidence = { status: 'FAIL', directory: path.relative(ROOT, directory) }
  try {
    evidence.host = bridge(bridgeScript(config))
    evidence.native = readReport(path.join(directory, 'probe.json'))
    evidence.status = validate(evidence.native, exists)
    if (evidence.host.error || evidence.host.electronExit !== 0 || ![0, 1].includes(evidence.host.pythonExit)) throw new Error(evidence.host.error || 'unobserved-native-exit')
    if (!Array.isArray(evidence.host.remaining) || !Array.isArray(evidence.host.cleanupErrors) || evidence.host.remaining.length || evidence.host.cleanupErrors.length) throw new Error('host-cleanup-failed')
    if ((evidence.status === 'PASS') !== (evidence.host.pythonExit === 0)) throw new Error('probe-exit-verdict-mismatch')
  } catch (error) { evidence.status = 'FAIL'; evidence.reason = error.message }
  // Always revalidate all journals; recovery cannot erase the original failure.
  try {
    evidence.recovery = bridge(recoveryScript(config))
    if (!Array.isArray(evidence.recovery.remaining) || !Array.isArray(evidence.recovery.cleanupErrors) || evidence.recovery.remaining.length || evidence.recovery.cleanupErrors.length) throw new Error('cleanup-failed')
  } catch (error) { evidence.status = 'FAIL'; evidence.cleanupFailure = error.message }
  return evidence
}
// Third-party recipients whose windows this host cannot let the harness own (shell handoff,
// hidden windows) may be accepted as explicit, reported gaps. Only BLOCKED, never FAIL, and
// never the browser or the overlay itself.
const GAP_APPS = ['explorer', 'vscode', 'terminal']
function acceptGaps(evidence, apps) {
  if (evidence.status !== 'BLOCKED' || !apps.length || !apps.every(app => GAP_APPS.includes(app))) return evidence
  const gaps = evidence.native.scenarios.filter(s => s.status !== 'PASS')
  if (!gaps.every(s => s.status === 'BLOCKED' && apps.includes(s.id.split('.')[0]))) return evidence
  return { ...evidence, status: 'PASS_WITH_GAPS', acceptedGaps: gaps.map(({ id, reason }) => ({ id, reason })) }
}
function main(argv = process.argv.slice(2)) {
  let output, evidence = { status: 'BLOCKED', reason: 'not-run' }
  try {
    const gapArg = argv.find(arg => arg.startsWith('--accept-gaps='))
    const rest = argv.filter(arg => arg !== gapArg)
    if (rest.length && (rest.length !== 2 || rest[0] !== '--evidence')) throw new Error('usage: native-overlay.cjs [--evidence worktree-local.json] [--accept-gaps=explorer,vscode,terminal]')
    if (rest.length) output = smoke.localOutput(ROOT, rest[1])
    const exe = smoke.prerequisites(ROOT), toWindows = p => smoke.command('wslpath', ['-w', p], 5000)
    const directory = fs.mkdtempSync(path.join(ROOT, 'desktop-native-overlay-')); fs.chmodSync(directory, 0o700)
    const config = { exe: toWindows(smoke.localRuntime(exe)), app: toWindows(path.join(ROOT, 'desktop')), root: toWindows(ROOT), directory: toWindows(directory) }
    evidence = acceptGaps(run({ config, directory }), gapArg ? gapArg.slice(14).split(',').filter(Boolean) : [])
    output ||= path.join(directory, 'report.json')
  } catch (error) { evidence.reason = error.message }
  if (output) { try { fs.mkdirSync(path.dirname(output), { recursive: true }); fs.writeFileSync(output, JSON.stringify(evidence, null, 2)) } catch (error) { evidence.status = 'FAIL'; evidence.evidenceError = error.message } }
  console.log(JSON.stringify(evidence)); return ['PASS', 'PASS_WITH_GAPS'].includes(evidence.status) ? 0 : 1
}
if (require.main === module) process.exitCode = main()
module.exports = { REQUIRED, APPS, ROUTES, sameIdentity, coordinate, validate, acceptGaps, bridgeScript, recoveryScript, nativeBridge, run, main }
