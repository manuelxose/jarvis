'use strict'

// CLI/main-only bounded window-state controller. Unlike the other probes,
// persistence stays ENABLED so save-then-restore can be proven against a
// harness-owned userData directory. No renderer API or supplied JS.
const fs = require('node:fs')
const path = require('node:path')
const PREFIX = 'desktop-native-window-state-'
const MODES = ['OVERLAY_COMPACT', 'OVERLAY_EXPANDED', 'COMMAND_CENTER_WINDOWED']
const OPERATIONS = ['observe', 'maximize', 'unmaximize', 'quit', ...MODES.map(m => `mode:${m}`)]
const OWNED_FILES = new Set(['window-state.json', 'window-state.json.tmp', 'request.json', 'response.json', 'response.json.tmp'])
// ponytail: Electron's top-level session files are matched by name; extend if a live run shows another.
const ELECTRON_FILES = /^(Preferences|Local State|Network Persistent State|TransportSecurity|Cookies|DIPS|SharedStorage|Trust Tokens|First Run|lockfile|Singleton\w*|Local Storage|Session Storage|declarative_performance_observer\.db)(-journal|-wal|-shm)?$/
const outside = rel => !rel || rel === '..' || rel.startsWith(`..${path.sep}`) || path.isAbsolute(rel)

function parse(argv, parseSmoke, root) {
  const args = argv.filter(a => a.startsWith('--window-state-probe-'))
  if (!args.length) return null
  const stateArgs = args.filter(a => a.startsWith('--window-state-probe-state='))
  const phaseArgs = args.filter(a => a.startsWith('--window-state-probe-phase='))
  if (stateArgs.length !== 1 || phaseArgs.length > 1 || args.length !== stateArgs.length + phaseArgs.length ||
    argv.some(a => a.startsWith('--smoke-') || a.startsWith('--overlay-probe-') || a.startsWith('--command-center-probe-'))) throw new Error('window-state-probe-arguments')
  const state = stateArgs[0].slice('--window-state-probe-state='.length)
  const phase = phaseArgs.length ? phaseArgs[0].slice('--window-state-probe-phase='.length) : 'seed'
  if (phase === 'seed') return { state: parseSmoke(['--smoke-entry=command-center', `--smoke-state=${state}`], root).state, phase }
  if (phase !== 'restore') throw new Error('window-state-probe-arguments')
  const realRoot = fs.realpathSync(root)
  if (!path.isAbsolute(state) || path.dirname(state) !== path.resolve(root) || !path.basename(state).startsWith(PREFIX)) throw new Error('window-state-probe-path')
  const stat = fs.lstatSync(state)
  if (stat.isSymbolicLink() || !stat.isDirectory() || path.dirname(fs.realpathSync(state)) !== realRoot) throw new Error('window-state-probe-path')
  for (const entry of fs.readdirSync(state, { withFileTypes: true })) {
    if (entry.isSymbolicLink() || !(entry.isDirectory() || OWNED_FILES.has(entry.name) || ELECTRON_FILES.test(entry.name))) throw new Error('window-state-probe-unexpected-file')
  }
  if (outside(path.relative(realRoot, fs.realpathSync(state)))) throw new Error('window-state-probe-path')
  return { state, phase }
}

function create({ state, later, quit, getWindows, getMode, requestMode, displays, io = fs }) {
  let last = 0
  const request = path.join(state, 'request.json'), response = path.join(state, 'response.json')
  function act(operation) {
    if (operation === 'observe' || operation === 'quit') return
    if (operation.startsWith('mode:')) { if (!requestMode(operation.slice(5))) throw new Error('mode-denied'); return }
    const win = getWindows().get('command-center')
    if (!win || win.isDestroyed() || !win.isVisible()) throw new Error('command-center-not-visible')
    if (operation === 'maximize') win.maximize(); else win.unmaximize()
  }
  async function poll() {
    try {
      if (io.existsSync(request)) {
        if (io.lstatSync(request).isSymbolicLink() || io.statSync(request).size > 1024) throw new Error('request-budget')
        const value = JSON.parse(io.readFileSync(request, 'utf8'))
        if (!Number.isInteger(value.id) || value.id < 1 || value.id > 120 || Object.keys(value).sort().join(',') !== 'id,operation' || !OPERATIONS.includes(value.operation)) throw new Error('request-invalid')
        if (value.id !== last) {
          if (value.id !== last + 1) throw new Error('request-order')
          act(value.operation)
          const windows = {}
          for (const [host, win] of getWindows()) {
            if (win.isDestroyed()) throw new Error('window-lost')
            const handle = win.getNativeWindowHandle()
            windows[host] = { hwnd: String(handle.length === 8 ? handle.readBigUInt64LE() : handle.readUInt32LE()),
              visible: win.isVisible(), maximized: win.isMaximized(), fullscreen: win.isFullScreen(), bounds: win.getBounds() }
          }
          const tmp = response + '.tmp'
          io.writeFileSync(tmp, JSON.stringify({ id: value.id, pid: process.pid, mode: getMode(), displays: displays(), windows }))
          for (let attempt = 0; ; attempt++) {
            try { io.renameSync(tmp, response); break }
            catch (e) { if (!['EPERM', 'EACCES', 'EBUSY'].includes(e.code) || attempt >= 5) throw e; await new Promise(resolve => later(resolve, 50)) }
          }
          last = value.id
          if (value.operation === 'quit') { quit(); return }
        }
      }
    } catch { quit(1); return }
    later(poll, 100)
  }
  later(() => quit(1), 120000)
  later(poll, 100)
}
module.exports = { parse, create, PREFIX, OPERATIONS }
