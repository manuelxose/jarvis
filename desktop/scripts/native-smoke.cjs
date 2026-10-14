'use strict'

// Linux Node orchestrates Windows PowerShell directly; no CMD, image-wide kill,
// renderer control channel, staged application copy, or Core process access.
const fs = require('node:fs')
const path = require('node:path')
const { spawnSync } = require('node:child_process')
const { createUiProtocol } = require('../src/ui-protocol')
const ENTRIES = ['command-center', 'overlay']
const STAGES = ['dist-ready', 'created', 'main-frame-ready', 'renderer-mounted', 'shown', 'closing-window', 'closing', 'window-closed', 'closed', 'quit']
const ROOT = path.resolve(__dirname, '../..')

function psLiteral(value) {
  if (typeof value !== 'string' || /[\x00-\x1f]/.test(value)) throw new Error('invalid-path')
  return `'${value.replaceAll("'", "''")}'`
}
function windowsArg(value) {
  psLiteral(value)
  return '"' + value.replace(/(\\*)"/g, '$1$1\\"').replace(/(\\+)$/g, '$1$1') + '"'
}
function localOutput(root, output) {
  const target = path.resolve(root, output), rel = path.relative(root, target)
  if (!rel || rel === '..' || rel.startsWith(`..${path.sep}`) || path.isAbsolute(rel)) throw new Error('evidence-outside-worktree')
  let ancestor = target
  while (!fs.existsSync(ancestor)) ancestor = path.dirname(ancestor)
  const realRel = path.relative(fs.realpathSync(root), fs.realpathSync(ancestor))
  if (realRel === '..' || realRel.startsWith(`..${path.sep}`) || path.isAbsolute(realRel)) throw new Error('evidence-symlink-escape')
  return target
}

// Shared by the live monitor and emergency recovery. Re-query path AND start
// time immediately before Stop-Process; PID reuse is never permission to kill.
const OWNERSHIP = `
function Discover {
  $rows = @(Get-CimInstance Win32_Process -Filter "Name = 'electron.exe'")
  if ($rows.Count -gt 512) { throw 'inventory-budget' }
  $changed = $true
  while ($changed) {
    $changed = $false
    foreach ($row in $rows) {
      if ($row.ExecutablePath -ne $exe) { continue }
      if (@($owned | Where-Object { $_.pid -eq $row.ProcessId }).Count -gt 0) { continue }
      $parent = $owned | Where-Object { $_.pid -eq $row.ParentProcessId } | Select-Object -First 1
      if ($null -eq $parent) { continue }
      $parentLive = Get-Process -Id $parent.pid -ErrorAction SilentlyContinue
      $child = Get-Process -Id $row.ProcessId -ErrorAction SilentlyContinue
      if ((Same-Process $parent $parentLive) -and $null -ne $child -and $child.Path -eq $exe -and $child.StartTime.Ticks -ge $parent.started) {
        if ($owned.Count -ge 512) { throw 'ownership-budget' }
        $script:owned += @{ pid=$child.Id; parent=$row.ParentProcessId; started="$($child.StartTime.Ticks)"; path=$child.Path; session=$child.SessionId }
        Write-Journal $script:owned
        $changed = $true
      }
    }
  }
}
function Write-Journal($records) {
  $json = ConvertTo-Json -InputObject @($records) -Depth 5 -Compress
  for ($i = 0; $i -lt 20; $i++) {
    try { Set-Content -LiteralPath $journal -Value $json -Encoding UTF8 -ErrorAction Stop; return } catch { if ($i -eq 19) { throw }; Start-Sleep -Milliseconds 50 }
  }
}
function Same-Process($record, $live) {
  return ($null -ne $live -and $record.path -eq $exe -and $live.Path -eq $exe -and "$($live.StartTime.Ticks)" -eq $record.started)
}
function Remaining {
  $alive = @()
  foreach ($record in $owned) {
    $live = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
    if (Same-Process $record $live) { $alive += $record }
  }
  return $alive
}
function Cleanup {
  foreach ($record in @(Remaining)) {
    $live = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
    if (Same-Process $record $live) {
      try { Stop-Process -InputObject $live -Force -ErrorAction Stop; $script:stopped += $record.pid }
      catch { $script:cleanupErrors += "stop-pid-$($record.pid)" }
    }
  }
  $end = (Get-Date).AddSeconds(5)
  do {
    $script:remaining = @(Remaining)
    if ($remaining.Count -eq 0) { break }
    Start-Sleep -Milliseconds 100
  } while ((Get-Date) -lt $end)
}
`

function monitorScript({ exe, app, state, directory, entry }) {
  const stdout = directory + '\\stdout.log', stderr = directory + '\\stderr.log', journal = directory + '\\owned.json'
  const args = [app, `--smoke-entry=${entry}`, `--smoke-state=${state}`].map(windowsArg).join(' ')
  return `$ErrorActionPreference = 'Stop'
$exe = ${psLiteral(exe)}
$journal = ${psLiteral(journal)}
$owned = @(); $windows = @(); $remaining = @(); $cleanupErrors = @(); $stopped = @()
$errorReason = $null; $exitCode = $null; $rootPid = $null; $survived = @()
${OWNERSHIP}
try {
  # Do not inherit an Electron CLI environment that disables the application.
  Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
  $proc = Start-Process -FilePath $exe -ArgumentList ${psLiteral(args)} -WorkingDirectory ${psLiteral(app)} -RedirectStandardOutput ${psLiteral(stdout)} -RedirectStandardError ${psLiteral(stderr)} -PassThru
  # Retain the native process handle while it is alive so ExitCode remains
  # observable after exit (Start-Process otherwise returns null on this host).
  $null = $proc.Handle
  $rootPid = $proc.Id
  $owned += @{ pid=$proc.Id; parent=$PID; started="$($proc.StartTime.Ticks)"; path=$proc.Path; session=$proc.SessionId }
  Write-Journal $owned
  if ($proc.Path -ne $exe) { throw 'launch-executable-mismatch' }
  $end = (Get-Date).AddSeconds(25)
  do {
    # Inventory while the parent is alive, before its short smoke hold ends.
    Discover
    foreach ($record in $owned) {
      $live = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
      if ((Same-Process $record $live) -and "$($live.MainWindowHandle)" -ne '0') {
        if (@($windows | Where-Object { $_.pid -eq $live.Id -and $_.handle -eq $live.MainWindowHandle }).Count -eq 0) {
          $windows += @{ pid=$live.Id; started=$record.started; path=$live.Path; session=$live.SessionId; title=$live.MainWindowTitle; handle="$($live.MainWindowHandle)" }
        }
      }
    }
    if ($proc.HasExited) { $exitCode = $proc.ExitCode; break }
    Start-Sleep -Milliseconds 100
  } while ((Get-Date) -lt $end)
  if (!$proc.HasExited) { throw 'lifecycle-timeout' }
  # Independent post-exit check, including every child recorded before exit.
  Start-Sleep -Milliseconds 500
  $survived = @(Remaining)
  if ($survived.Count -gt 0) { throw 'owned-child-survival' }
} catch { $errorReason = $_.Exception.Message }
finally {
  try { Discover } catch { $cleanupErrors += 'cleanup-inventory-failed' }
  Cleanup
}
@{ rootPid=$rootPid; owned=@($owned); windows=@($windows); exitCode=$exitCode; error=$errorReason; survived=@($survived); remaining=@($remaining); cleanupErrors=@($cleanupErrors); stopped=@($stopped); languageMode="$($ExecutionContext.SessionState.LanguageMode)" } | ConvertTo-Json -Depth 6 -Compress
`
}

function recoveryScript(exe, journal) {
  return `$ErrorActionPreference = 'Stop'
$exe = ${psLiteral(exe)}
$journal = ${psLiteral(journal)}
$owned = @(); $remaining = @(); $cleanupErrors = @(); $stopped = @()
${OWNERSHIP}
if (Test-Path -LiteralPath ${psLiteral(journal)}) {
  $decoded = Get-Content -Raw -LiteralPath ${psLiteral(journal)} | ConvertFrom-Json
  $owned = @($decoded)
  try { Discover } catch { $cleanupErrors += 'cleanup-inventory-failed' }
  Cleanup
} else { $cleanupErrors += 'ownership-journal-unavailable' }
@{ remaining=@($remaining); cleanupErrors=@($cleanupErrors); stopped=@($stopped) } | ConvertTo-Json -Depth 5 -Compress
`
}

function command(executable, args, timeout = 45000) {
  const result = spawnSync(executable, args, { encoding: 'utf8', timeout, maxBuffer: 4 * 1024 * 1024, shell: false })
  if (result.error) throw new Error(`bridge-${result.error.code || 'error'}`)
  if (result.status !== 0) throw new Error(`bridge-exit-${result.status}: ${String(result.stderr).slice(0, 6000)}`)
  return result.stdout.replace(/^\uFEFF/, '').trim()
}
function powershell(script) {
  return JSON.parse(command('powershell.exe', ['-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(script, 'utf16le').toString('base64')]))
}
function parseMarkers(text) {
  const records = []
  for (const line of text.split(/\r?\n/)) {
    const prefix = '[jarvis-bootstrap] '
    if (line.startsWith(prefix)) records.push(JSON.parse(line.slice(prefix.length)))
  }
  return records
}
function validate(report, markers, entry, exe) {
  if (!report || !Array.isArray(report.owned) || !Array.isArray(report.windows) || !Array.isArray(report.remaining) || !Array.isArray(report.cleanupErrors) || !Array.isArray(report.survived) || report.owned.length > 512 || report.windows.length > 512) throw new Error('malformed-inventory')
  if (report.cleanupErrors.length || report.remaining.length) throw new Error('cleanup-failed')
  if (report.error) throw new Error(report.error)
  if (report.survived?.length) throw new Error('owned-child-survival')
  if (report.exitCode !== 0) throw new Error('electron-exit-nonzero-or-unobserved')
  const root = report.owned.find(p => p.pid === report.rootPid)
  const identity = p => p.path === exe && Number.isSafeInteger(p.pid) && p.pid > 0 && typeof p.started === 'string' && /^\d+$/.test(p.started)
  if (!root || !identity(root)) throw new Error('unowned-root')
  const reachable = new Set([root.pid])
  if (new Set(report.owned.map(p => p.pid)).size !== report.owned.length || report.owned.some(p => !identity(p))) throw new Error('unowned-process')
  const byPid = new Map(report.owned.map(p => [p.pid, p]))
  for (let i = 0; i < report.owned.length; i++) {
    const before = reachable.size
    for (const p of report.owned) {
      const parent = byPid.get(p.parent)
      if (reachable.has(p.parent) && BigInt(parent.started) <= BigInt(p.started)) reachable.add(p.pid)
    }
    if (reachable.size === before) break
  }
  if (reachable.size !== report.owned.length) throw new Error('unowned-process')
  if (!report.windows.some(w => w.pid === root.pid && w.started === root.started && w.path === exe && Number.isInteger(w.session) && w.session > 0 && /^\d+$/.test(String(w.handle)) && BigInt(w.handle) > 0n && typeof w.title === 'string' && w.title.length)) throw new Error('missing-native-hwnd')
  if (markers.some(m => m.stage === 'failure' || m.stage === 'duplicate-instance')) throw new Error('bootstrap-failure')
  let cursor = -1
  for (const stage of STAGES) {
    const next = markers.findIndex((m, i) => i > cursor && m.stage === stage && m.pid === root.pid && (!m.entry || m.entry === entry))
    if (next < 0) throw new Error(`missing-or-unordered-marker:${stage}`)
    cursor = next
  }
  if (markers.find(m => m.stage === 'quit').code !== 0) throw new Error('bootstrap-quit-nonzero')
}

// A small orchestration seam: tests supply bridge responses, never a fake
// runtime executable or fake renderer in the native CLI.
function readBoundedLog(file) {
  if (fs.statSync(file).size > 4 * 1024 * 1024) throw new Error('bootstrap-log-budget')
  return fs.readFileSync(file, 'utf8')
}
function runEntries({ exe, app, directory, toWindows, bridge = powershell, readLog = readBoundedLog }) {
  const runs = []
  for (const entry of ENTRIES) {
    const local = path.join(directory, entry)
    fs.mkdirSync(local)
    const run = { entry, status: 'FAIL', directory: local }
    runs.push(run)
    let winDirectory
    try {
      winDirectory = toWindows(local)
      const config = { exe, app, directory: winDirectory, state: winDirectory + '\\state', entry }
      run.native = bridge(monitorScript(config))
      run.markers = parseMarkers(readLog(path.join(local, 'stdout.log')))
      validate(run.native, run.markers, entry, exe)
      run.status = 'PASS'
    } catch (error) {
      run.reason = error.message
      // Bridge death, malformed output or log failure does not skip cleanup.
      try {
        if (!winDirectory) throw new Error('not-launched-path-conversion-failed')
        run.recovery = bridge(recoveryScript(exe, winDirectory + '\\owned.json'))
        if (!Array.isArray(run.recovery.remaining) || !Array.isArray(run.recovery.cleanupErrors) || run.recovery.remaining.length || run.recovery.cleanupErrors.length) run.cleanupFailure = 'cleanup-failed'
      } catch (cleanup) { run.cleanupFailure = cleanup.message }
      break // No second launch after a failure/uncertain cleanup.
    }
  }
  return { status: runs.length === 2 && runs.every(r => r.status === 'PASS') ? 'PASS' : 'FAIL', runs }
}

function prerequisites(root, invoke = command) {
  invoke('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', 'Write-Output bridge-ready'], 10000)
  const executable = path.join(root, 'desktop/node_modules/electron/dist/electron.exe')
  if (!fs.existsSync(executable)) throw new Error('missing-windows-electron: provision desktop lockfile win32 binary in this worktree')
  if (fs.realpathSync(executable) !== executable) throw new Error('electron-symlink-rejected')
  try { createUiProtocol(path.join(root, 'ui/dist')).preflight() }
  catch { throw new Error('missing-or-incomplete-ui-dist: npm --prefix ui run build') }
  return executable
}

// Chromium cannot launch its sandboxed renderer from an executable on a UNC
// (\\\\wsl.localhost) path: measured 0/3 from UNC vs 3/3 from local NTFS. Only the
// stock Electron runtime is cached locally, keyed by version; app code, ui/dist
// and smoke state stay in the worktree.
function localRuntime(executable, invoke = command) {
  const dist = path.dirname(executable)
  const version = JSON.parse(fs.readFileSync(path.join(dist, '../package.json'), 'utf8')).version
  if (!/^\d+\.\d+\.\d+(-[\w.]+)?$/.test(version)) throw new Error('electron-version-invalid')
  const base = invoke('wslpath', ['-u', invoke('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', 'Write-Output $env:LOCALAPPDATA'], 10000)], 5000)
  const target = path.join(base, 'jarvis', 'electron-runtime', version)
  if (!fs.existsSync(path.join(target, 'electron.exe'))) {
    const partial = `${target}.partial-${process.pid}`
    fs.rmSync(partial, { recursive: true, force: true })
    fs.mkdirSync(path.dirname(target), { recursive: true })
    fs.cpSync(dist, partial, { recursive: true })
    fs.renameSync(partial, target)
  }
  return path.join(target, 'electron.exe')
}

function main(argv = process.argv.slice(2)) {
  const evidence = { status: 'FAIL', runs: [] }
  let output
  try {
    if (argv.length && (argv.length !== 2 || argv[0] !== '--evidence')) throw new Error('usage: native-smoke.cjs [--evidence worktree-local.json]')
    if (argv.length) output = localOutput(ROOT, argv[1])
    const toWindows = p => command('wslpath', ['-w', p], 5000)
    const executable = prerequisites(ROOT)
    // Fresh directory inside the worktree: no preferences or singleton overlap.
    const directory = fs.mkdtempSync(path.join(ROOT, 'desktop-native-smoke-'))
    fs.chmodSync(directory, 0o700)
    Object.assign(evidence, runEntries({ exe: toWindows(localRuntime(executable)), app: toWindows(path.join(ROOT, 'desktop')), directory, toWindows }))
  } catch (error) { evidence.reason = error.message }
  if (output) {
    try { fs.mkdirSync(path.dirname(output), { recursive: true }); fs.writeFileSync(output, JSON.stringify(evidence, null, 2)) }
    catch (error) { evidence.status = 'FAIL'; evidence.evidenceError = error.message }
  }
  console.log(JSON.stringify(evidence))
  return evidence.status === 'PASS' ? 0 : 1
}
if (require.main === module) process.exitCode = main()
module.exports = { OWNERSHIP, localRuntime, readBoundedLog, prerequisites, psLiteral, windowsArg, localOutput, monitorScript, recoveryScript, command, powershell, parseMarkers, validate, runEntries, main, STAGES }
