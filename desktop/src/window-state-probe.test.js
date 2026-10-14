'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const probe = require('./window-state-probe')
const { parseSmoke } = require('./main')

function root(t) {
  const dir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'wsp-root-')))
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }))
  return dir
}
const arg = (state, phase) => [`--window-state-probe-state=${state}`, ...(phase ? [`--window-state-probe-phase=${phase}`] : [])]

test('no probe arguments is inert', () => {
  assert.equal(probe.parse([], () => { throw new Error('called') }, '/nowhere'), null)
})

test('seed creates a new directory and defaults to the seed phase; an existing one is rejected', t => {
  const dir = root(t), state = path.join(dir, `${probe.PREFIX}a`)
  assert.deepEqual(probe.parse(arg(state), parseSmoke, dir), { state, phase: 'seed' })
  assert.ok(fs.statSync(state).isDirectory())
  assert.throws(() => probe.parse(arg(state, 'seed'), parseSmoke, dir), /smoke-state-exists/)
})

test('restore accepts an owned prefixed directory with only harness and Electron files', t => {
  const dir = root(t), state = path.join(dir, `${probe.PREFIX}a`)
  fs.mkdirSync(path.join(state, 'Local Storage'), { recursive: true })
  for (const f of ['window-state.json', 'request.json', 'response.json', 'Preferences']) fs.writeFileSync(path.join(state, f), '{}')
  assert.deepEqual(probe.parse(arg(state, 'restore'), parseSmoke, dir), { state, phase: 'restore' })
})

test('restore rejects missing, non-prefixed, symlinked, out-of-root, nested and file-polluted directories', t => {
  const dir = root(t), outside = root(t)
  const make = name => { const p = path.join(dir, name); fs.mkdirSync(p); return p }
  assert.throws(() => probe.parse(arg(path.join(dir, `${probe.PREFIX}missing`), 'restore'), parseSmoke, dir))
  assert.throws(() => probe.parse(arg(make('plain'), 'restore'), parseSmoke, dir), /path/)
  const target = make(`${probe.PREFIX}target`)
  const link = path.join(dir, `${probe.PREFIX}link`); fs.symlinkSync(target, link)
  assert.throws(() => probe.parse(arg(link, 'restore'), parseSmoke, dir), /path/)
  const foreign = path.join(outside, `${probe.PREFIX}x`); fs.mkdirSync(foreign)
  assert.throws(() => probe.parse(arg(foreign, 'restore'), parseSmoke, dir), /path/)
  fs.mkdirSync(path.join(target, `${probe.PREFIX}nested`))
  assert.throws(() => probe.parse(arg(path.join(target, `${probe.PREFIX}nested`), 'restore'), parseSmoke, dir), /path/)
  assert.throws(() => probe.parse(arg('relative', 'restore'), parseSmoke, dir), /path/)
  for (const name of ['evil.exe', 'notes.txt']) {
    const polluted = make(`${probe.PREFIX}p-${name}`); fs.writeFileSync(path.join(polluted, name), 'x')
    assert.throws(() => probe.parse(arg(polluted, 'restore'), parseSmoke, dir), /unexpected-file/)
  }
  const linked = make(`${probe.PREFIX}l`); fs.symlinkSync(outside, path.join(linked, 'Cache'))
  assert.throws(() => probe.parse(arg(linked, 'restore'), parseSmoke, dir), /unexpected-file/)
})

test('combined, duplicated, unknown-phase and unknown probe arguments are rejected', t => {
  const dir = root(t), state = path.join(dir, `${probe.PREFIX}a`)
  const bad = [
    [...arg(state), '--smoke-entry=overlay'], [...arg(state), `--overlay-probe-state=${state}`], [...arg(state), `--command-center-probe-state=${state}`],
    [...arg(state), ...arg(state)], arg(state, 'bogus'), ['--window-state-probe-phase=seed'], [...arg(state), '--window-state-probe-extra=1'],
  ]
  for (const args of bad) assert.throws(() => probe.parse(args, () => { throw new Error('must not create state') }, dir), /window-state-probe-arguments|must not/)
  assert.equal(fs.existsSync(state), false)
})

function controller(t, files, operations) {
  const dir = root(t), jobs = [], calls = []
  const win = { isDestroyed: () => false, getNativeWindowHandle: () => Buffer.alloc(8, 1), isVisible: () => true, isMaximized: () => false, isFullScreen: () => false, getBounds: () => ({ x: 1, y: 2, width: 3, height: 4 }), maximize: () => calls.push('maximize'), unmaximize: () => calls.push('unmaximize') }
  probe.create({ state: dir, later: (fn, ms) => jobs.push([fn, ms]), quit: code => calls.push(['quit', code]), getWindows: () => new Map([['command-center', win]]), getMode: () => 'COMMAND_CENTER_WINDOWED', requestMode: m => { calls.push(m); return operations !== 'deny' }, displays: () => [] })
  return { dir, jobs, calls, run: async () => { const [fn] = jobs.splice(0).find(([, ms]) => ms === 100) || []; await fn?.() } }
}

test('controller answers allow-listed operations in id order and rejects everything else by quitting with failure', async t => {
  const c = controller(t)
  const send = async (id, operation) => { fs.writeFileSync(path.join(c.dir, 'request.json'), JSON.stringify({ id, operation })); await c.run() }
  await send(1, 'mode:COMMAND_CENTER_WINDOWED')
  assert.equal(JSON.parse(fs.readFileSync(path.join(c.dir, 'response.json'))).id, 1)
  assert.deepEqual(c.calls, ['COMMAND_CENTER_WINDOWED'])
  await send(2, 'maximize'); assert.deepEqual(c.calls.slice(1), ['maximize'])
  const response = JSON.parse(fs.readFileSync(path.join(c.dir, 'response.json')))
  assert.equal(response.windows['command-center'].visible, true)
  await send(4, 'observe'); assert.deepEqual(c.calls.at(-1), ['quit', 1])
})

test('controller denies unlisted modes, eval-like operations, extra keys and oversize requests', async t => {
  for (const body of [{ id: 1, operation: 'mode:HIDDEN' }, { id: 1, operation: 'mode:COMMAND_CENTER_FULLSCREEN' }, { id: 1, operation: 'eval' }, { id: 1, operation: 'observe', script: 'x' }, { id: 0, operation: 'observe' }, { id: 1, operation: 'x'.repeat(2000) }]) {
    const c = controller(t)
    fs.writeFileSync(path.join(c.dir, 'request.json'), JSON.stringify(body)); await c.run()
    assert.deepEqual(c.calls, [['quit', 1]], JSON.stringify(body).slice(0, 60))
  }
  const denied = controller(t, null, 'deny')
  fs.writeFileSync(path.join(denied.dir, 'request.json'), JSON.stringify({ id: 1, operation: 'mode:OVERLAY_EXPANDED' })); await denied.run()
  assert.deepEqual(denied.calls, ['OVERLAY_EXPANDED', ['quit', 1]])
})

test('quit operation replies then exits cleanly and the lifetime is bounded to 120s', async t => {
  const c = controller(t)
  assert.ok(c.jobs.some(([, ms]) => ms === 120000))
  fs.writeFileSync(path.join(c.dir, 'request.json'), JSON.stringify({ id: 1, operation: 'quit' })); await c.run()
  assert.deepEqual(c.calls, [['quit', undefined]])
  assert.ok(fs.existsSync(path.join(c.dir, 'response.json')))
})
