'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { psLiteral, windowsArg, localOutput, runEntries, validate, parseMarkers, command, powershell, recoveryScript, monitorScript, prerequisites, localRuntime, readBoundedLog, STAGES } = require('./native-smoke.cjs')
const EXE = 'C:\\work tree\\electron.exe'
function fixture(t) {
  const dir = fs.mkdtempSync(path.resolve('native-smoke-fixture-'))
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }))
  return dir
}
function good() {
  const root = { pid: 120, parent: 10, started: '638000000000000001', path: EXE, session: 1 }
  return { rootPid: 120, owned: [root, { ...root, pid: 121, parent: 120, started: '638000000000000002' }], windows: [{ ...root, title: 'Jarvis', handle: '12345' }], exitCode: 0, remaining: [], survived: [], cleanupErrors: [] }
}
function markers(entry = 'command-center') { return STAGES.map(stage => ({ stage, pid: 120, entry, code: 0 })) }
function log(entry) { return 'unrelated Chromium diagnostic\n' + markers(entry).map(m => '[jarvis-bootstrap] ' + JSON.stringify(m)).join('\r\n') }
function run(t, bridge, readLog = p => log(path.basename(path.dirname(p)))) {
  return runEntries({ exe: EXE, app: 'C:\\work tree\\desktop', directory: fixture(t), toWindows: p => 'C:\\work tree\\' + path.basename(p), bridge, readLog })
}

test('two sequential entries require full lifecycle, native identity, HWND and zero survivors', t => {
  let calls = 0
  const result = run(t, () => { calls++; return good() })
  assert.equal(result.status, 'PASS'); assert.equal(calls, 2)
  assert.deepEqual(result.runs.map(r => r.entry), ['command-center', 'overlay'])
  assert.notEqual(result.runs[0].directory, result.runs[1].directory)
})

for (const [name, mutate, reason] of [
  ['load failure', r => { r.error = 'main-frame-load' }, /main-frame-load/],
  ['timeout', r => { r.error = 'lifecycle-timeout' }, /lifecycle-timeout/],
  ['missing HWND', r => { r.windows = [] }, /missing-native-hwnd/],
  ['zero HWND', r => { r.windows[0].handle = '0' }, /missing-native-hwnd/],
  ['wrong window identity (PID reuse)', r => { r.windows[0].started = '638000000000000002' }, /missing-native-hwnd/],
  ['missing session', r => { r.windows[0].session = 0 }, /missing-native-hwnd/],
  ['wrong executable', r => { r.owned[0].path = 'C:\\other\\electron.exe' }, /unowned-root/],
  ['unowned child', r => { r.owned[1].parent = 777 }, /unowned-process/],
  ['child older than parent', r => { r.owned[1].started = '638000000000000000' }, /unowned-process/],
  ['duplicate PID', r => { r.owned[1].pid = 120 }, /unowned-process/],
  ['unobserved exit', r => { r.exitCode = null }, /electron-exit/],
  ['nonzero exit', r => { r.exitCode = 1 }, /electron-exit/],
  ['child survived despite successful cleanup', r => { r.survived = [r.owned[1]] }, /owned-child-survival/],
  ['cleanup failed', r => { r.cleanupErrors = ['denied'] }, /cleanup-failed/],
  ['remaining child', r => { r.remaining = [r.owned[1]] }, /cleanup-failed/],
  ['malformed inventory', r => { r.owned = null }, /malformed-inventory/],
  ['inventory over budget', r => { r.owned = Array(513).fill(r.owned[0]) }, /malformed-inventory/],
  ['missing independent cleanup inventory', r => { delete r.survived }, /malformed-inventory/],
]) test(name + ' stays FAIL, invokes recovery and prevents second launch', t => {
  let calls = 0
  const result = run(t, () => {
    if (++calls === 1) { const r = good(); mutate(r); return r }
    return { remaining: [], cleanupErrors: [] }
  })
  assert.equal(result.status, 'FAIL'); assert.equal(result.runs.length, 1); assert.equal(calls, 2)
  assert.match(result.runs[0].reason, reason)
})

test('bridge loss/timeout attempts bounded recovery and records recovery error', t => {
  let calls = 0
  const result = run(t, () => { calls++; throw new Error(calls === 1 ? 'bridge-ETIMEDOUT' : 'bridge-ENOENT') })
  assert.equal(result.status, 'FAIL'); assert.equal(calls, 2)
  assert.equal(result.runs[0].reason, 'bridge-ETIMEDOUT')
  assert.equal(result.runs[0].cleanupFailure, 'bridge-ENOENT')
})
test('survivors in emergency recovery never become PASS', t => {
  let calls = 0
  const result = run(t, () => { if (++calls === 1) throw new Error('bridge-loss'); return { remaining: [120], cleanupErrors: [] } })
  assert.equal(result.status, 'FAIL'); assert.equal(result.runs[0].cleanupFailure, 'cleanup-failed')
})
test('malformed stdout and missing logs still recover', t => {
  for (const readLog of [() => '[jarvis-bootstrap] nope', () => { throw new Error('ENOENT') }]) {
    let calls = 0
    const result = run(t, () => ++calls === 1 ? good() : { remaining: [], cleanupErrors: [] }, readLog)
    assert.equal(result.status, 'FAIL'); assert.equal(calls, 2)
  }
})
for (const stage of STAGES) test(`missing ${stage} cannot pass`, () => {
  assert.throws(() => validate(good(), markers().filter(m => m.stage !== stage), 'command-center', EXE), /missing-or-unordered-marker/)
})
test('wrong entry/PID, out-of-order stages, duplicate instance and failure diagnostics cannot pass', () => {
  for (const list of [markers('overlay'), markers().map(m => ({ ...m, pid: 999 })), markers().reverse(), [...markers(), { stage: 'failure' }], [...markers(), { stage: 'duplicate-instance' }]]) assert.throws(() => validate(good(), list, 'command-center', EXE))
  const list = markers(); list.at(-1).code = 1
  assert.throws(() => validate(good(), list, 'command-center', EXE), /quit-nonzero/)
  assert.deepEqual(parseMarkers(log('overlay')), markers('overlay'))
})

test('argument quoting handles spaces, apostrophes, quotes and trailing backslashes; rejects controls', () => {
  assert.equal(psLiteral("C:\\a'; Stop-Process *; #"), "'C:\\a''; Stop-Process *; #'")
  assert.equal(windowsArg('C:\\work tree\\'), '"C:\\work tree\\\\"')
  assert.equal(windowsArg('a"b'), '"a\\"b"')
  for (const value of ['a\n', 'a\0', 'a\r', null]) assert.throws(() => windowsArg(value), /invalid-path/)
})
test('evidence rejects outside paths and symlinked ancestors', t => {
  const root = fixture(t), outside = fixture(t)
  assert.equal(localOutput(root, 'evidence/new.json'), path.join(root, 'evidence/new.json'))
  assert.throws(() => localOutput(root, '../escape.json'), /outside-worktree/)
  fs.symlinkSync(outside, path.join(root, 'link'))
  assert.throws(() => localOutput(root, 'link/new.json'), /symlink-escape/)
})
test('log read budget is enforced before parsing', t => {
  const file = path.join(fixture(t), 'stdout.log')
  fs.writeFileSync(file, 'ok')
  assert.equal(readBoundedLog(file), 'ok')
  fs.writeFileSync(file, 'x'.repeat(4 * 1024 * 1024 + 1))
  assert.throws(() => readBoundedLog(file), /bootstrap-log-budget/)
})
test('missing bridge, failed subprocess and hung subprocess have explicit bounded errors', () => {
  assert.throws(() => command('native-smoke-no-such-executable', [], 100), /bridge-ENOENT/)
  assert.throws(() => command(process.execPath, ['-e', 'process.exit(3)'], 1000), /bridge-exit-3/)
  assert.throws(() => command(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], 100), /bridge-ETIMEDOUT/)
})

// Optional extra contract: execute the production recovery functions against
// cmdlet doubles in the actual constrained bridge. No Electron is launched.
let hasBridge = false
try { hasBridge = command('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', 'Write-Output ready'], 10000) === 'ready' } catch {}
test('PowerShell recovery stops only exact identity; skips reused/unowned PIDs', { skip: !hasBridge }, () => {
  const doubles = `
$script:live = @{
  120=@{ Id=120; Path=${psLiteral(EXE)}; StartTime=@{Ticks=638000000000000001} }
  121=@{ Id=121; Path=${psLiteral(EXE)}; StartTime=@{Ticks=638000000000000099} }
  122=@{ Id=122; Path='C:\\other\\electron.exe'; StartTime=@{Ticks=638000000000000001} }
}
function Get-CimInstance { return @() }
function Test-Path { return $true }
function Get-Content { return '[{"pid":120,"path":"C:\\\\work tree\\\\electron.exe","started":"638000000000000001"},{"pid":121,"path":"C:\\\\work tree\\\\electron.exe","started":"638000000000000001"},{"pid":122,"path":"C:\\\\work tree\\\\electron.exe","started":"638000000000000001"}]' }
function Get-Process { param($Id, $ErrorAction) return $script:live[[int]$Id] }
function Stop-Process { param($InputObject, [switch]$Force, $ErrorAction) $script:live.Remove([int]$InputObject.Id) }
`
  const result = powershell(doubles + recoveryScript(EXE, 'C:\\journal.json'))
  assert.deepEqual(result.stopped, [120])
  assert.deepEqual(result.remaining, [])
  assert.deepEqual(result.cleanupErrors, [])
})

test('preflight explicitly rejects missing bridge, executable and incomplete build', t => {
  const root = fixture(t)
  assert.throws(() => prerequisites(root, () => { throw new Error('bridge-ENOENT') }), /bridge-ENOENT/)
  assert.throws(() => prerequisites(root, () => 'ready'), /missing-windows-electron/)
  const exe = path.join(root, 'desktop/node_modules/electron/dist/electron.exe')
  fs.mkdirSync(path.dirname(exe), { recursive: true }); fs.writeFileSync(exe, 'not launched')
  assert.throws(() => prerequisites(root, () => 'ready'), /missing-or-incomplete-ui-dist/)
  fs.mkdirSync(path.join(root, 'ui/dist/assets'), { recursive: true })
  for (const entry of ['index.html', 'overlay.html']) fs.writeFileSync(path.join(root, 'ui/dist', entry), '<div id="root"></div><script src="/assets/app.js"></script>')
  fs.writeFileSync(path.join(root, 'ui/dist/assets/app.js'), 'export const fixture = true')
  assert.equal(prerequisites(root, () => 'ready'), exe)
})

test('production PowerShell monitor captures descendants and window then checks exit independently', { skip: !hasBridge }, () => {
  const doubles = `
$script:root = @{ Id=120; Path=${psLiteral(EXE)}; StartTime=@{Ticks=638000000000000001}; SessionId=1; HasExited=$true; ExitCode=0; MainWindowHandle=12345; MainWindowTitle='Jarvis' }
$script:child = @{ Id=121; Path=${psLiteral(EXE)}; StartTime=@{Ticks=638000000000000002}; SessionId=1; MainWindowHandle=[IntPtr]::Zero }
$script:gone = $false
function Start-Process { return $script:root }
function Set-Content { }
function Get-CimInstance { return @{ ProcessId=121; ParentProcessId=120; ExecutablePath=${psLiteral(EXE)} } }
function Get-Process { param($Id, $ErrorAction) if ($script:gone) { return $null }; if ($Id -eq 120) { return $script:root }; if ($Id -eq 121) { return $script:child } }
function Start-Sleep { param($Milliseconds) if ($Milliseconds -eq 500) { $script:gone=$true } }
function Stop-Process { throw 'unexpected-stop' }
`
  const result = powershell(doubles + monitorScript({ exe: EXE, app: 'C:\\work tree\\desktop', state: 'C:\\work tree\\state', directory: 'C:\\work tree\\logs', entry: 'command-center' }))
  assert.equal(result.error, null)
  assert.equal(result.owned.length, 2)
  assert.deepEqual(result.owned.map(p => p.pid), [120, 121])
  assert.equal(result.owned[1].parent, 120)
  assert.equal(result.owned[1].started, '638000000000000002')
  assert.equal(result.windows.length, 1, 'native IntPtr.Zero is not a window')
  assert.equal(result.windows[0].handle, '12345')
  assert.deepEqual(result.remaining, []); assert.deepEqual(result.stopped, [])
  validate(result, markers(), 'command-center', EXE)
})

test('localRuntime caches the stock runtime on a local path once, keyed by version', t => {
  const root = fixture(t)
  const dist = path.join(root, 'desktop/node_modules/electron/dist')
  fs.mkdirSync(path.join(dist, 'locales'), { recursive: true })
  fs.writeFileSync(path.join(dist, 'electron.exe'), 'x'); fs.writeFileSync(path.join(dist, 'locales/en.pak'), 'y')
  fs.writeFileSync(path.join(dist, '../package.json'), '{"version":"44.4.5"}')
  const local = fs.mkdtempSync(path.join(root, 'localappdata-'))
  const bridge = (exe) => exe === 'wslpath' ? local : 'C:\\Users\\x\\AppData\\Local'
  const exe = localRuntime(path.join(dist, 'electron.exe'), bridge)
  assert.equal(exe, path.join(local, 'jarvis/electron-runtime/44.4.5/electron.exe'))
  assert.equal(fs.readFileSync(path.join(path.dirname(exe), 'locales/en.pak'), 'utf8'), 'y')
  fs.writeFileSync(exe, 'cached')
  assert.equal(fs.readFileSync(localRuntime(path.join(dist, 'electron.exe'), bridge), 'utf8'), 'cached')
  fs.writeFileSync(path.join(dist, '../package.json'), '{"version":"../x"}')
  assert.throws(() => localRuntime(path.join(dist, 'electron.exe'), bridge), /electron-version-invalid/)
})
