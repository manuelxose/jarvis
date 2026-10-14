'use strict'

// Import is inert. Linux controller launches the actual active worktree on
// Windows and delegates decoding/comparison to Electron nativeImage, not npm.
const fs = require('node:fs')
const crypto = require('node:crypto')
const path = require('node:path')
const smoke = require('./native-smoke.cjs')
const { REFERENCES, HOSTS, SIZES, PER_CHANNEL_TOLERANCE, MAX_DIFF_RATIO, baselinePath } = require('../src/visual-probe')
const ROOT = path.resolve(__dirname, '../..')
const utc = script => script.replace(/\.StartTime\.Ticks/g, '.StartTime.ToUniversalTime().Ticks')
function bridgeScript({ exe, app, directory, baseline = null }) {
  const state = directory + '\\state'
  const requests = [{ id:1, operation:'observe' }]
  for (const host of HOSTS) for (const size of Object.keys(SIZES)) requests.push({ id:requests.length + 1, operation:'capture', host, size })
  requests.push({ id:requests.length + 1, operation:'quit' })
  const args = [app, `--visual-probe-state=${state}`, ...(baseline ? [`--visual-probe-baseline=${baseline}`] : [])].map(smoke.windowsArg).join(' ')
  return utc(`$ErrorActionPreference = 'Stop'
$exe = ${smoke.psLiteral(exe)}
$journal = ${smoke.psLiteral(directory + '\\owned.json')}
$state = ${smoke.psLiteral(state)}
$owned=@(); $remaining=@(); $cleanupErrors=@(); $stopped=@()
$scenarios=@(); $displays=@(); $errorReason=$null; $exitCode=$null; $rootPid=$null
${smoke.OWNERSHIP}
try {
  Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
  $proc = Start-Process -FilePath $exe -ArgumentList ${smoke.psLiteral(args)} -WorkingDirectory ${smoke.psLiteral(app)} -RedirectStandardOutput ${smoke.psLiteral(directory + '\\stdout.log')} -RedirectStandardError ${smoke.psLiteral(directory + '\\stderr.log')} -PassThru
  $null=$proc.Handle; $rootPid=$proc.Id
  $owned += @{ pid=$proc.Id; parent=$PID; started="$($proc.StartTime.Ticks)"; path=$proc.Path; session=$proc.SessionId }
  Write-Journal $owned
  if ($proc.Path -ne $exe) { throw 'launch-executable-mismatch' }
  $end=(Get-Date).AddSeconds(20)
  while (!(Test-Path -LiteralPath $state)) {
    if ($proc.HasExited) { throw 'visual-exited-before-ready' }
    if ((Get-Date) -gt $end) { throw 'visual-ready-timeout' }
    Discover; Start-Sleep -Milliseconds 100
  }
  $requests = ConvertFrom-Json -InputObject ${smoke.psLiteral(JSON.stringify(requests))}
  foreach ($request in $requests) {
    $json=ConvertTo-Json -InputObject $request -Compress
    $tmp=Join-Path $state 'request.json.tmp'; $target=Join-Path $state 'request.json'
    # Fixed requests contain ASCII only. Cmdlet avoids BOM and works in
    # ConstrainedLanguage, where constructing Text.UTF8Encoding is forbidden.
    Set-Content -LiteralPath $tmp -Value $json -Encoding Ascii
    for ($attempt=0; ; $attempt++) {
      try { Move-Item -LiteralPath $tmp -Destination $target -Force; break }
      catch {
        if ($attempt -ge 5) { throw }
        Start-Sleep -Milliseconds 100
      }
    }
    $end=(Get-Date).AddSeconds(25); $reply=$null
    do {
      Discover
      $file=Join-Path $state 'response.json'
      if (Test-Path -LiteralPath $file) {
        if ((Get-Item -LiteralPath $file).Length -gt 65536) { throw 'visual-response-budget' }
        $candidate=Get-Content -Raw -LiteralPath $file | ConvertFrom-Json
        if ($candidate.id -eq $request.id) { $reply=$candidate; break }
      }
      if ($proc.HasExited) { throw 'visual-exited-before-response' }
      Start-Sleep -Milliseconds 100
    } while ((Get-Date) -lt $end)
    if ($null -eq $reply) { throw 'visual-response-timeout' }
    if ($reply.pid -ne $rootPid) { throw 'visual-response-unowned' }
    $displays=@($reply.displays); $scenarios += @($reply.scenarios)
  }
  # Cmdlet wait is permitted in ConstrainedLanguage; Process.WaitForExit is not.
  if (!$proc.HasExited) {
    try { Wait-Process -Id $rootPid -Timeout 5 -ErrorAction Stop }
    catch { throw 'visual-quit-timeout' }
  }
  $exitCode=$proc.ExitCode
} catch { $errorReason=$_.Exception.Message }
finally {
  try { Discover } catch { $cleanupErrors += 'cleanup-inventory-failed' }
  Cleanup
}
@{ platform='win32'; rootPid=$rootPid; owned=@($owned); scenarios=@($scenarios); displays=@($displays); exitCode=$exitCode; error=$errorReason; remaining=@($remaining); cleanupErrors=@($cleanupErrors); stopped=@($stopped) } | ConvertTo-Json -Depth 10 -Compress
`)
}
function bridge(script) {
  return JSON.parse(smoke.command('powershell.exe', ['-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(script, 'utf16le').toString('base64')], 220000))
}
const slash = value => typeof value === 'string' ? value.replaceAll('\\', '/') : value
function aggregate(rows, exists = () => false, baseline = null) {
  const scenarios = REFERENCES.map(ref => {
    const expected = baseline && !ref.reason ? baselinePath(ref.id) : ref.reference
    const base = { id:ref.id, reference:expected, capture:null, status:'BLOCKED', ratio:null, reason:ref.reason }
    // Unreachable design states may never be promoted by controller output.
    if (ref.reason) return base
    const matches = Array.isArray(rows) ? rows.filter(r => r?.id === ref.id) : []
    if (matches.length !== 1) return { ...base, status:'FAIL', reason:matches.length ? 'duplicate-comparison' : 'missing-capture-result' }
    const row = { ...matches[0], capture:slash(matches[0].capture), diff:slash(matches[0].diff) }
    if (!['PASS', 'FAIL', 'BLOCKED'].includes(row.status)) return { ...base, status:'FAIL', reason:'unknown-status' }
    if (row.status === 'BLOCKED') return { ...base, reason:typeof row.reason === 'string' && row.reason.trim() ? row.reason : 'missing-blocked-reason' }
    if (row.reference !== expected || !row.capture || !exists(row.capture, ref.size, 'capture')) return { ...base, status:'FAIL', reason:'missing-or-invalid-capture' }
    if (row.status === 'FAIL') return { ...row, reason:typeof row.reason === 'string' && row.reason.trim() ? row.reason : 'comparison-failed' }
    if (!Number.isFinite(row.ratio) || row.ratio < 0 || row.ratio > MAX_DIFF_RATIO || !Number.isInteger(row.diffPixels) || row.diffPixels < 0 || row.diffPixels / (SIZES[ref.size][0] * SIZES[ref.size][1]) !== row.ratio || !row.diff || !exists(row.diff, ref.size, 'diff')) return { ...row, status:'FAIL', reason:'invalid-or-excessive-diff' }
    return { ...row, reason:null }
  })
  const unknown = !Array.isArray(rows) || rows.some(r => !r || !REFERENCES.some(ref => ref.id === r.id) || !['PASS', 'FAIL', 'BLOCKED'].includes(r.status))
  const status = unknown || scenarios.some(r => r.status === 'FAIL') ? 'FAIL' : scenarios.some(r => r.status === 'BLOCKED') ? 'BLOCKED' : 'PASS'
  return { status, scenarios, ...(unknown ? { reason:'unknown-or-malformed-scenario' } : {}) }
}
function pngExists(directory, artifact, size, kind, root = ROOT) {
  try {
    const file = smoke.localOutput(root, artifact)
    if (path.dirname(file) !== path.join(directory, 'state') || !path.basename(file).startsWith(kind + '-') || path.extname(file) !== '.png') return false
    const stat = fs.lstatSync(file)
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size < 33 || stat.size > 32 * 1024 * 1024) return false
    const fd = fs.openSync(file, 'r'), header = Buffer.alloc(24)
    try { fs.readSync(fd, header, 0, 24, 0) } finally { fs.closeSync(fd) }
    return header.subarray(0, 8).equals(Buffer.from([137,80,78,71,13,10,26,10])) && header.toString('ascii',12,16) === 'IHDR' && header.readUInt32BE(16) === SIZES[size][0] && header.readUInt32BE(20) === SIZES[size][1]
  } catch { return false }
}
function baselineHashes() {
  return REFERENCES.filter(r => !r.reason).map(r => {
    const file = baselinePath(r.id)
    try { return { id:r.id, file, sha256:crypto.createHash('sha256').update(fs.readFileSync(path.join(ROOT, file))).digest('hex') } }
    catch { return { id:r.id, file, sha256:null } }
  })
}
function run({ config, directory, invoke = bridge, exists = (f, s, k) => pngExists(directory, f, s, k), hashes = baselineHashes }) {
  const baseline = config.baseline || null
  let evidence = { status:'FAIL', directory:path.relative(ROOT, directory), referenceSet:baseline ? 'win32-baseline' : 'phase2-linux', ...(baseline ? { baselines:hashes() } : {}) }
  try {
    const host = invoke(bridgeScript(config))
    evidence = { ...evidence, ...aggregate(host.scenarios, exists, baseline), host }
    if (host.platform !== 'win32' || host.error || host.exitCode !== 0 || !Array.isArray(host.remaining) || !Array.isArray(host.cleanupErrors) || host.remaining.length || host.cleanupErrors.length) throw new Error(host.error || 'native-exit-or-cleanup-failed')
    const root = host.owned?.find(p => p.pid === host.rootPid)
    if (!root || root.path !== config.exe || !Number.isInteger(root.pid) || root.pid <= 0 || !/^\d+$/.test(root.started) || !Array.isArray(host.owned) || host.owned.length > 512) throw new Error('invalid-host-ownership')
  } catch (e) {
    evidence.status = 'FAIL'; evidence.reason = e.message
    evidence.scenarios ||= aggregate([], undefined, baseline).scenarios
  }
  // Recover from bridge timeout / malformed stdout using the owned PID journal.
  try {
    evidence.recovery = invoke(utc(smoke.recoveryScript(config.exe, config.directory + '\\owned.json')))
    if (!Array.isArray(evidence.recovery.remaining) || !Array.isArray(evidence.recovery.cleanupErrors) || evidence.recovery.remaining.length || evidence.recovery.cleanupErrors.length) throw new Error('recovery-cleanup-failed')
  } catch (e) { evidence.status = 'FAIL'; evidence.cleanupFailure = e.message }
  return evidence
}
function main(argv = process.argv.slice(2)) {
  let output, config
  let evidence = { generatedAt:new Date().toISOString(), perChannelTolerance:PER_CHANNEL_TOLERANCE, maxDiffRatio:MAX_DIFF_RATIO, status:'BLOCKED', scenarios:REFERENCES.map(ref => ({ id:ref.id, reference:ref.reference, capture:null, ratio:null, status:'BLOCKED', reason:ref.reason || 'preflight-not-run' })) }
  try {
    const usage = 'usage: native-visual.cjs [--baseline win32] [--evidence worktree-local.json]'
    let baseline = null
    for (let i = 0; i < argv.length; i += 2) {
      if (argv[i] === '--evidence' && output === undefined && i + 1 < argv.length) output = smoke.localOutput(ROOT, argv[i + 1])
      else if (argv[i] === '--baseline' && argv[i + 1] === 'win32' && !baseline) baseline = 'win32'
      else throw new Error(usage)
    }
    if (baseline) evidence.referenceSet = 'win32-baseline'
    const executable = smoke.prerequisites(ROOT)
    const win = p => smoke.command('wslpath', ['-w', p], 5000)
    const directory = fs.mkdtempSync(path.join(ROOT, 'desktop-native-visual-')); fs.chmodSync(directory, 0o700)
    output ||= path.join(directory, 'report.json')
    config = { exe:win(smoke.localRuntime(executable)), app:win(path.join(ROOT, 'desktop')), directory:win(directory), baseline:evidence.referenceSet === 'win32-baseline' ? 'win32' : null }
    evidence = { ...evidence, ...run({ config, directory }) }
  } catch (e) { evidence.reason = e.message; evidence.scenarios.forEach(r => { if (r.reason === 'preflight-not-run') r.reason = e.message }) }
  if (output) {
    try { fs.mkdirSync(path.dirname(output), { recursive:true }); fs.writeFileSync(output, JSON.stringify(evidence, null, 2)) }
    catch (e) { evidence.status = 'FAIL'; evidence.evidenceError = e.message }
  }
  console.log(JSON.stringify(evidence)); return evidence.status === 'PASS' ? 0 : 1
}
if (require.main === module) process.exitCode = main()
module.exports = { bridgeScript, aggregate, pngExists, run, main, baselineHashes }
