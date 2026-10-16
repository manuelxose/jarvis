'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const fs = require('node:fs')
const path = require('node:path')
const { createUiProtocol, parseRoute, allowedNavigation, CSP } = require('./ui-protocol')
const { startDesktop, parseSmoke, MOUNT_CHECK } = require('./main')
const store = require('./window-state-store')
const vm = require('node:vm')
const { createRequire } = require('node:module')

for (const [name, runtime, starts] of [
  ['Electron dynamically imported browser entry', { versions: { electron: '44.4.5' }, type: 'browser' }, true],
  ['ordinary Node import', { versions: {}, type: undefined }, false],
  ['Electron renderer import', { versions: { electron: '44.4.5' }, type: 'renderer' }, false],
]) test(`${name} has the intended bootstrap side effects without require.main`, () => {
  const filename = path.join(__dirname, 'main.js'), localRequire = createRequire(filename)
  const logs = [], app = new EventEmitter()
  let locks = 0, electronImports = 0
  Object.assign(app, {
    getPath: () => __dirname,
    commandLine: { appendSwitch() {} }, disableHardwareAcceleration() {},
    requestSingleInstanceLock() { locks++; return false },
    quit() { app.emit('quit', {}, 0) },
  })
  function importedRequire(id) {
    if (id !== 'electron') return localRequire(id)
    electronImports++
    return { app, protocol: { registerSchemesAsPrivileged() {} } }
  }
  const context = { require: importedRequire, module: { exports: {} }, __dirname,
    process: { ...runtime, argv: [], pid: 42 }, setTimeout, clearTimeout,
    console: { log: line => logs.push(line) } }
  // Execute the tracked production CommonJS module with Electron's import
  // semantics; this is behavior, not a source-string assertion.
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), context, { filename })
  assert.equal(electronImports, starts ? 1 : 0)
  assert.equal(locks, starts ? 1 : 0)
  assert.equal(logs.some(line => line.includes('duplicate-instance')), starts)
  assert.equal(typeof context.module.exports.startDesktop, 'function')
})

test('preload filters malformed messages and unsubscribes idempotently without exposing native escape hatches', async () => {
  let bridge, answer = 'OVERLAY_COMPACT'
  const ipc = new EventEmitter(), sent = []
  ipc.invoke = async () => answer
  ipc.send = (...args) => sent.push(args)
  const filename = path.join(__dirname, 'preload.js')
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), { require: () => ({
    contextBridge: { exposeInMainWorld(name, value) { assert.equal(name, 'jarvisDesktop'); bridge = value } }, ipcRenderer: ipc,
  }) }, { filename })
  assert.deepEqual(Object.keys(bridge).sort(), ['core', 'settings', 'getMode', 'moveOverlayTo', 'onModeChanged', 'requestMode', 'setOverlayInteractive'].sort())
  assert.equal(await bridge.getMode(), 'OVERLAY_COMPACT')
  answer = { evil: true }; assert.equal(await bridge.getMode(), null)
  for (const value of [null, 42, {}, 'invalid']) { bridge.requestMode(value); bridge.setOverlayInteractive(value) }
  for (const coords of [[NaN, 0], [0, Infinity], ['1', 0], [100001, 0]]) bridge.moveOverlayTo(...coords)
  assert.deepEqual(sent, [])
  bridge.requestMode('OVERLAY_EXPANDED'); bridge.setOverlayInteractive(false); bridge.moveOverlayTo(1, 2)
  assert.equal(sent.length, 3)
  assert.doesNotThrow(() => bridge.onModeChanged(null)())
  const observed = [], cleanup = bridge.onModeChanged(value => observed.push(value))
  const lateCallback = ipc.listeners('mode:changed')[0]
  ipc.emit('mode:changed', {}, 'invalid'); ipc.emit('mode:changed', {}, 'OVERLAY_EXPANDED')
  assert.deepEqual(observed, ['OVERLAY_EXPANDED'])
  cleanup(); cleanup(); lateCallback({}, 'OVERLAY_COMPACT')
  assert.equal(ipc.listenerCount('mode:changed'), 0)
  assert.deepEqual(observed, ['OVERLAY_EXPANDED'])
})

function fixture(t) {
  const dir = fs.mkdtempSync(path.resolve('bootstrap-fixture-'))
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }))
  const dist = path.join(dir, 'dist')
  fs.mkdirSync(path.join(dist, 'assets'), { recursive: true })
  for (const name of ['index.html', 'overlay.html']) fs.writeFileSync(path.join(dist, name), '<div id="root"></div><script type="module" src="/assets/app.js"></script><link href="/assets/app.css" rel="stylesheet"><link rel="icon" href="/favicon.svg">')
  fs.writeFileSync(path.join(dist, 'assets/app.js'), 'export const mounted = true')
  fs.writeFileSync(path.join(dist, 'assets/app.css'), 'body {}')
  fs.writeFileSync(path.join(dist, 'favicon.svg'), '<svg xmlns="http://www.w3.org/2000/svg"></svg>')
  return { dir, dist }
}

const primaryArea = { id: 1, workArea: { x: 0, y: 0, width: 1920, height: 1080 } }
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve() }
function fakeBridge() {
  const bridge = { stops: 0, starts: 0, controls: [], state: 'connecting', stateFns: [], eventFns: [] }
  Object.assign(bridge, {
    start() { this.starts++ }, stop() { this.stops++ }, getState() { return this.state }, getReplay() { return [{ v: 1, name: 'core.snapshot' }] },
    onState(fn) { this.stateFns.push(fn) }, onEvent(fn) { this.eventFns.push(fn) },
    async stopCore() { this.stopCalls = (this.stopCalls || 0) + 1; if (this.stopReject) throw Object.assign(new Error('x'), { code: 'unreachable' }) },
    async control(action) { this.controls.push(action); if (this.reject) throw Object.assign(new Error('secret-token-123 leaked'), { code: 'unauthorized' }) },
  })
  return bridge
}

test('settings preload validates payloads, denies non-booleans locally and disposes listeners', async () => {
  let bridge, answer, calls = []
  const ipc = new EventEmitter()
  ipc.invoke = async (...args) => { calls.push(args); return answer }
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'preload.js'), 'utf8'), { require: () => ({
    contextBridge: { exposeInMainWorld(_name, value) { bridge = value } }, ipcRenderer: ipc,
  }) })
  const valid = { launchAtLogin: false, shortcuts: [
    { action: 'toggleOverlay', accelerator: 'Control+Alt+Shift+J', registered: false },
    { action: 'toggleCommandCenter', accelerator: 'Control+Alt+Shift+C', registered: true },
  ] }
  answer = valid
  assert.deepEqual(await bridge.settings.get(), valid)
  for (const bad of [null, 1, 'true', {}, undefined]) assert.equal(await bridge.settings.setLaunchAtLogin(bad), null)
  assert.equal(calls.length, 1)
  assert.deepEqual(await bridge.settings.setLaunchAtLogin(true), valid)
  assert.deepEqual(calls.at(-1), ['desktop:set-launch-at-login', true])
  const changed = []
  const off = bridge.settings.onChanged(value => changed.push(value))
  const listener = ipc.listeners('desktop:settings')[0]
  for (const bad of [null, {}, { ...valid, launchAtLogin: 1 }, { ...valid, shortcuts: [] },
    { ...valid, shortcuts: [valid.shortcuts[0], valid.shortcuts[0]] },
    { ...valid, shortcuts: [{ ...valid.shortcuts[0], registered: 'true' }, valid.shortcuts[1]] }]) {
    answer = bad
    assert.equal(await bridge.settings.get(), null)
    assert.equal(await bridge.settings.setLaunchAtLogin(false), null)
    listener({}, bad)
  }
  listener({}, valid)
  off(); off(); listener({}, valid)
  assert.equal(changed.length, 1)
  assert.equal(ipc.listenerCount('desktop:settings'), 0)
  let opens = 0
  const offOpen = bridge.settings.onOpenRequested(() => opens++)
  const open = ipc.listeners('desktop:open-settings')[0]
  open({}, {}); open({}, 'settings'); open({})
  offOpen(); offOpen(); open({})
  assert.equal(opens, 1)
  assert.equal(ipc.listenerCount('desktop:open-settings'), 0)
  ipc.invoke = async () => { throw new Error('IPC closed') }
  await assert.rejects(bridge.settings.get(), /IPC closed/)
})

test('settings IPC restricts login writes to the owned center main frame and broadcasts truthful state', async t => {
  const h = harness(t, { shortcutBlocked: ['Control+Alt+Shift+J'] })
  await h.tick(0)
  const overlay = h.wins[0]
  h.control.requestMode('COMMAND_CENTER_WINDOWED'); await h.tick(0)
  const center = h.wins.find(w => w.settings.title === 'Jarvis command-center')
  const get = h.handlers.get('desktop:get-settings'), set = h.handlers.get('desktop:set-launch-at-login')
  const stranger = { sender: {}, senderFrame: center.webContents.mainFrame }
  assert.equal(await get(stranger), null)
  assert.equal(await set(stranger, true), null)
  assert.equal(await set(h.owned(overlay), true), null)
  assert.equal(await set({ ...h.owned(center), senderFrame: { url: 'app://command-center/' } }, true), null)
  const event = h.owned(center)
  const originalUrl = event.senderFrame.url
  event.senderFrame.url = 'https://untrusted.invalid/'
  assert.equal(await get(event), null); assert.equal(await set(event, true), null)
  event.senderFrame.url = originalUrl
  for (const bad of [null, 'true', 1, {}, [], undefined]) assert.equal(await set(event, bad), null)
  assert.equal(h.native.loginSets.length, 0)
  const before = await get(event)
  assert.equal(before.launchAtLogin, false)
  assert.equal(before.shortcuts[0].registered, false)
  assert.equal(before.shortcuts[1].registered, true)
  const result = await set(event, true)
  assert.equal(result.launchAtLogin, true)
  assert.equal(h.native.loginSets.length, 1)
  assert.equal(h.writes.at(-1)[1].launchAtLogin, true)
  for (const win of [center, overlay]) assert.deepEqual(win.webContents.sent.at(-1), ['desktop:settings', result])
  assert.deepEqual(await get(h.owned(overlay)), result)
  h.control.quit()
  assert.equal(await get(event), null); assert.equal(await set(event, false), null)
})

test('settings IPC reports unchanged state after registry or persistence failure', async t => {
  for (const failure of ['loginReject', 'stateReject']) {
    const config = {}
    const h = harness(t, config)
    await h.tick(0)
    h.control.requestMode('COMMAND_CENTER_WINDOWED'); await h.tick(0)
    config[failure] = true
    const center = h.wins.find(w => w.settings.title === 'Jarvis command-center')
    const result = await h.handlers.get('desktop:set-launch-at-login')(h.owned(center), true)
    assert.equal(result.launchAtLogin, false)
    assert.ok(h.stages().includes('login-item-failed'))
    assert.equal(h.app.exitCode, undefined)
  }
})

function harness(t, config = {}) {
  const coreBridge = config.coreBridge || fakeBridge()
  const logs = [], wins = [], jobs = new Map(), handlers = new Map(), writes = []
  let now = 0, counter = 0
  const timers = {
    setTimeout(fn, ms) { const id = ++counter; jobs.set(id, { fn, at: now + ms }); return id },
    clearTimeout(id) { jobs.delete(id) },
  }
  async function tick(ms) {
    const end = now + ms
    for (;;) {
      const next = [...jobs].sort((a, b) => a[1].at - b[1].at)[0]
      if (!next || next[1].at > end) break
      now = next[1].at; jobs.delete(next[0]); next[1].fn(); await flush()
    }
    now = end; await flush()
  }
  const app = new EventEmitter()
  Object.assign(app, {
    paths: { userData: path.resolve('unused-test-state') }, exits: [], quits: 0,
    setPath(key, val) { this.paths[key] = val }, getPath(key) { return this.paths[key] },
    requestSingleInstanceLock: () => config.lock !== false,
    whenReady: () => config.appHang ? new Promise(() => {}) : config.appReject ? Promise.reject(new Error('ready failed')) : Promise.resolve(),
    commandLine: { appendSwitch() {} }, disableHardwareAcceleration() {},
    quit() { this.quits++; this.emit('before-quit'); if (!config.quitHang) this.emit('quit', {}, 0) },
    exit(code) { this.exits.push(code); this.emit('quit', {}, code) },
  })
  class Window extends EventEmitter {
    constructor(settings) {
      super(); this.settings = settings; this.dead = false; this.visible = false; this.positions = []; this.ignore = []
      this.bounds = { x: settings.x, y: settings.y, width: settings.width, height: settings.height }
      this.normalBounds = { ...this.bounds }; this.fullscreen = false; this.fullscreenCalls = []
      this.webContents = new EventEmitter()
      Object.assign(this.webContents, {
        mainFrame: { url: '' }, sent: [], scripts: [],
        setWindowOpenHandler(fn) { this.popup = fn }, send(...args) { this.sent.push(args) },
        executeJavaScript(script) { this.scripts.push(script); return config.mountReject ? Promise.reject(new Error('observe failed')) : config.mountHang ? new Promise(() => {}) : Promise.resolve(config.mounted !== false) },
      })
      wins.push(this)
    }
    isDestroyed() { return this.dead }
    isVisible() { return this.visible }
    show() { this.visible = true }
    hide() { this.visible = false }
    focus() { this.focused = true }
    maximize() { this.maximized = true; this.bounds = { x: 0, y: 0, width: 1920, height: 1080 }; this.emit('resize'); this.emit('maximize') }
    unmaximize() { this.maximized = false; this.bounds = { ...this.normalBounds }; this.emit('resize'); this.emit('unmaximize') }
    minimize() { this.minimized = true; this.bounds = { x: -32000, y: -32000, width: 160, height: 30 }; this.emit('resize') }
    restore() { this.minimized = false; this.bounds = { ...this.normalBounds }; this.emit('resize') }
    isMinimized() { return Boolean(this.minimized) }
    isMaximized() { return Boolean(this.maximized) }
    isFullScreen() { return this.fullscreen }
    nativeFullscreen(val) {
      if (config.fullscreenSurfacesWindow) this.visible = true
      this.fullscreen = val
      this.bounds = val ? { x: 0, y: 0, width: 1920, height: 1080 } : { ...this.normalBounds }
      this.emit('resize'); this.emit(val ? 'enter-full-screen' : 'leave-full-screen')
    }
    setFullScreen(val) {
      this.fullscreenCalls.push(val)
      if (config.fullscreenReject) throw new Error('private native error')
      if (!config.fullscreenDeferred && this.fullscreen !== val) this.nativeFullscreen(val)
    }
    setIgnoreMouseEvents(...args) { this.ignore.push(args) }
    setSize(...args) { this.size = args }
    setBounds(bounds) {
      this.size = [bounds.width, bounds.height]
      this.bounds = { ...bounds }
      if (!this.fullscreen && !this.maximized) this.normalBounds = { ...bounds }
    }
    setPosition(...args) { this.positions.push(args) }
    getBounds() { return { ...this.bounds } }
    getNormalBounds() { return { ...this.normalBounds } }
    loadURL(url) {
      this.webContents.mainFrame.url = url
      if (config.loadReject) return Promise.reject(new Error('load failed with private URL'))
      if (!config.loadHang) this.webContents.emit('did-finish-load')
      return Promise.resolve()
    }
    close() { const e = { prevented: false, preventDefault() { this.prevented = true } }; this.emit('close', e); if (!e.prevented) this.destroy() }
    destroy() { if (!this.dead) { this.dead = true; this.visible = false; this.emit('closed') } }
  }
  const ipcMain = new EventEmitter()
  ipcMain.handle = (key, fn) => handlers.set(key, fn)
  const protocol = { registerSchemesAsPrivileged(entries) { this.schemes = entries }, handle(scheme, fn) { this.handler = fn } }
  const screen = Object.assign(config.plainScreen ? {} : new EventEmitter(), { displays: config.displays || [primaryArea],
    getAllDisplays() { if (config.displayThrows) throw new Error('private display failure'); return this.displays }, getPrimaryDisplay() { return this.displays[0] },
    getDisplayMatching(bounds) { return this.displays.find(d => d.id === store.displayForBounds(bounds, this.displays)) || this.displays[0] } })
  if (config.rawState !== undefined) {
    const dir = fs.mkdtempSync(path.resolve('bootstrap-state-'))
    t.after(() => fs.rmSync(dir, { recursive: true, force: true }))
    fs.writeFileSync(path.join(dir, 'window-state.json'), config.rawState)
    app.paths.userData = dir
  }
  const stateStore = { ...store, ...(config.rawState === undefined ? { loadStateWithDiagnostics: () => ({ state: { ...store.DEFAULT_STATE, initialMode: config.initialMode || 'OVERLAY_COMPACT', ...config.state }, recovered: null }) } : {}), saveState: (...args) => { if (config.stateReject) throw new Error('disk error'); writes.push(structuredClone(args)) } }
  const ui = config.ui || { preflight() { if (config.distReject) throw new Error('missing') }, respond() {} }
  const native = { trays: [], templates: [], registered: new Map(), registerCalls: [], unregisterAll: 0, dialogs: [], loginSets: [], login: { openAtLogin: config.loginInitially === true }, dialogResponse: 0 }
  class Tray extends EventEmitter {
    constructor(icon) { super(); this.icon = icon; this.destroyed = 0; native.trays.push(this) }
    setToolTip(text) { this.tooltip = text }
    setContextMenu(menu) { this.menu = menu }
    destroy() { this.destroyed++ }
  }
  const Menu = { buildFromTemplate(template) { native.templates.push(template); return { template } } }
  const globalShortcut = {
    register(accelerator, fn) {
      native.registerCalls.push(accelerator)
      if (config.shortcutThrows) throw new Error('boom')
      if ((config.shortcutBlocked || []).includes(accelerator)) return false
      native.registered.set(accelerator, fn); return true
    },
    unregisterAll() { native.unregisterAll++; native.registered.clear() },
  }
  const dialog = { async showMessageBox(opts) { native.dialogs.push(opts); if (native.dialogReject) throw new Error('dialog unavailable'); return { response: native.dialogResponse } } }
  const nativeImage = { createFromPath: p => ({ path: p, resize(size) { return { path: p, size, toPNG: () => Buffer.from('png'), addRepresentation() {} } } }) }
  Object.assign(app, {
    getLoginItemSettings: () => ({ ...native.login }),
    setLoginItemSettings(settings) { native.loginSets.push(settings); if (config.loginReject) throw new Error('registry unavailable'); native.login.openAtLogin = settings.openAtLogin },
    getAppPath: () => path.resolve('.'), getName: () => 'jarvis-desktop',
  })
  const electron = { app, BrowserWindow: Window, ipcMain, protocol, screen, ...(config.noNative ? {} : { Tray, Menu, globalShortcut, dialog, nativeImage }) }
  const control = startDesktop(electron, { ui, stateStore, timers, log: record => logs.push(record), smoke: config.smoke || null, probe: config.probe || null, centerProbe: config.centerProbe || null, windowProbe: config.windowProbe || null, trayProbe: config.trayProbe || null, visualProbe: config.visualProbe || null, coreBridge })
  t.after(() => control.quit())
  return { native, coreBridge, app, screen, wins, jobs, handlers, ipcMain, protocol, writes, logs, control, tick, stages: () => logs.map(r => r.stage), owned(win) { return { sender: win.webContents, senderFrame: win.webContents.mainFrame } } }
}

for (const host of ['command-center', 'overlay']) test(`protocol serves ${host} entry and root-relative Vite assets with MIME/CSP`, async t => {
  const { dist } = fixture(t), ui = createUiProtocol(dist)
  ui.preflight()
  const response = ui.respond({ url: `app://${host}/` })
  assert.equal(response.status, 200)
  assert.equal(response.headers.get('content-type'), 'text/html')
  assert.equal(response.headers.get('content-security-policy'), CSP)
  assert.match(await response.text(), /root/)
  const icon = ui.respond({ url: `app://${host}/favicon.svg` })
  assert.equal(icon.status, 200); assert.equal(icon.headers.get('content-type'), 'image/svg+xml')
  for (const [file, mime] of [['app.js', 'text/javascript'], ['app.css', 'text/css']]) {
    const asset = ui.respond({ url: `app://${host}/assets/${file}` })
    assert.equal(asset.status, 200); assert.equal(asset.headers.get('content-type'), mime)
    assert.equal(asset.headers.get('x-content-type-options'), 'nosniff')
  }
})

test('protocol rejects malformed origins, cross-entry HTML, traversal, separators and Windows ADS', t => {
  const { dist } = fixture(t), ui = createUiProtocol(dist)
  for (const url of [null, '', 'https://overlay/', 'app://unknown/', 'app://overlay:9/', 'app://user@overlay/', 'app://overlay/../index.html', 'app://overlay/%2e%2e/index.html', 'app://overlay/assets%2fapp.js', 'app://overlay/assets%5capp.js', 'app://overlay/assets/%252e%252e/app.js', 'app://overlay/assets/../../index.html', 'app://overlay/assets/./app.js', 'app://overlay/index.html', 'app://command-center/overlay.html', 'app://overlay/assets/a%00.js', 'app://overlay/assets/%zz.js', 'app://overlay/assets/a:b.js', 'app://overlay/assets/a%3ab.js', 'app://overlay/assets/dir./a.js', 'app://overlay/assets\\app.js', 'app://overlay/private.json', 'app://overlay/' + 'x'.repeat(4096)]) assert.equal(ui.respond({ url }).status, 403, String(url))
  assert.deepEqual(parseRoute('app://overlay/overlay.html#view'), { host: 'overlay', file: 'overlay.html' })
  assert.equal(allowedNavigation('app://overlay/', 'overlay'), true)
  assert.equal(allowedNavigation('app://command-center/', 'overlay'), false)
  assert.equal(allowedNavigation('app://overlay/assets/app.js', 'overlay'), false)
})

test('protocol returns deterministic missing/unreadable errors and rejects symlink escapes', async t => {
  const { dist, dir } = fixture(t), ui = createUiProtocol(dist)
  assert.equal(ui.respond({ url: 'app://overlay/assets/missing.js' }).status, 404)
  fs.writeFileSync(path.join(dir, 'outside.js'), 'secret')
  fs.symlinkSync(path.join(dir, 'outside.js'), path.join(dist, 'assets/escape.js'))
  const escape = ui.respond({ url: 'app://overlay/assets/escape.js' })
  assert.equal(escape.status, 403); assert.equal(await escape.text(), 'forbidden')
  const unreadable = createUiProtocol(dist, { ...fs, readFileSync() { throw Object.assign(new Error('private location'), { code: 'EACCES' }) } })
  const response = unreadable.respond({ url: 'app://overlay/assets/app.js' })
  assert.equal(response.status, 500); assert.equal(await response.text(), 'unreadable asset')
  assert.throws(() => unreadable.preflight())
})

test('preflight requires both entries, root, JS and every directly referenced built asset', t => {
  const { dist } = fixture(t), ui = createUiProtocol(dist)
  fs.unlinkSync(path.join(dist, 'overlay.html')); assert.throws(() => ui.preflight())
  fs.writeFileSync(path.join(dist, 'overlay.html'), '<div></div>'); assert.throws(() => ui.preflight(), /incomplete/)
  fs.writeFileSync(path.join(dist, 'overlay.html'), '<div id="root"></div>'); assert.throws(() => ui.preflight(), /incomplete/)
  fs.writeFileSync(path.join(dist, 'overlay.html'), '<div id="root"></div><script src="/assets/no.js"></script>'); assert.throws(() => ui.preflight())
  fs.rmSync(dist, { recursive: true }); assert.throws(() => ui.preflight())
})

for (const entry of ['command-center', 'overlay']) test(`smoke ${entry} waits for DOM mount then shows/closes/quits without timers or windows`, async t => {
  const h = harness(t, { smoke: { entry, state: path.resolve('unused-smoke-state') }, mounted: false })
  await flush()
  assert.equal(h.wins.length, 1)
  const win = h.wins[0]
  assert.equal(win.visible, false)
  assert.deepEqual(win.settings.webPreferences, { preload: path.join(__dirname, 'preload.js'), sandbox: true, contextIsolation: true, nodeIntegration: false, devTools: false })
  assert.equal(win.webContents.mainFrame.url, `app://${entry}/`)
  win.webContents.executeJavaScript = script => { assert.equal(script, MOUNT_CHECK); return Promise.resolve(true) }
  await h.tick(50)
  assert.equal(win.visible, true)
  assert.equal(h.writes.length, 0)
  for (const key of ['userData', 'sessionData', 'crashDumps']) assert.equal(h.app.paths[key], path.resolve('unused-smoke-state'))
  await h.tick(1500)
  assert.equal(win.dead, true); assert.equal(h.jobs.size, 0); assert.equal(h.app.quits, 1)
  const stages = h.stages()
  for (const stage of ['dist-ready', 'created', 'main-frame-ready', 'renderer-mounted', 'shown', 'closing-window', 'window-closed', 'closing', 'closed', 'quit']) assert.ok(stages.includes(stage), stage)
  assert.ok(stages.indexOf('renderer-mounted') < stages.indexOf('shown'))
})

test('window-state probe keeps persistence active, pins userData and ignores the persisted initial mode', async t => {
  const state = path.resolve('unused-window-state')
  const h = harness(t, { windowProbe: { state, phase: 'restore' }, initialMode: 'COMMAND_CENTER_WINDOWED' }); await flush()
  assert.equal(h.wins.length, 1)
  assert.equal(h.handlers.get('mode:get')(h.owned(h.wins[0])), 'OVERLAY_COMPACT')
  for (const key of ['userData', 'sessionData', 'crashDumps']) assert.equal(h.app.paths[key], state)
  const before = h.writes.length
  h.wins[0].emit('move')
  assert.equal(h.writes.length, before + 1)
  assert.ok(h.writes.length > 0)
  h.ipcMain.emit('mode:request', h.owned(h.wins[0]), 'COMMAND_CENTER_WINDOWED')
  assert.equal(h.handlers.get('mode:get')(h.owned(h.wins[0])), 'OVERLAY_COMPACT')
})

for (const initialMode of ['HIDDEN', 'OVERLAY_COMPACT', 'OVERLAY_EXPANDED', 'COMMAND_CENTER_WINDOWED', 'COMMAND_CENTER_FULLSCREEN']) test(`ordinary initial mode ${initialMode} remains supported`, async t => {
  const h = harness(t, { initialMode }); await flush()
  assert.deepEqual(h.app.exits, [])
  assert.equal(h.jobs.size, 0)
  if (initialMode === 'HIDDEN') assert.equal(h.wins.length, 0)
  else {
    const win = h.wins.at(-1)
    assert.equal(win.visible, true); assert.equal(h.handlers.get('mode:get')(h.owned(win)), initialMode)
    if (initialMode.startsWith('COMMAND_CENTER')) assert.equal(win.fullscreen, initialMode === 'COMMAND_CENTER_FULLSCREEN')
  }
  h.control.quit(); assert.equal(h.jobs.size, 0); assert.ok(h.wins.every(w => w.dead))
})

test('duplicate instance creates no windows or timers', async t => {
  const h = harness(t, { lock: false }); await flush()
  assert.ok(h.stages().includes('duplicate-instance')); assert.equal(h.wins.length, 0); assert.equal(h.jobs.size, 0); assert.equal(h.app.quits, 1)
})

for (const [name, config, reason, delay] of [
  ['missing dist', { distReject: true }, 'dist-missing-or-incomplete', 0],
  ['loadURL rejection', { loadReject: true }, 'load-url-rejected', 0],
  ['app readiness rejection', { appReject: true }, 'bootstrap-ready', 0],
  ['app readiness timeout', { appHang: true }, 'app-ready-timeout', 15000],
  ['main frame never loaded', { loadHang: true }, 'renderer-timeout', 15000],
  ['root never mounted', { mounted: false }, 'renderer-timeout', 15000],
  ['mount observation rejected', { mountReject: true }, 'mount-observation', 0],
  ['mount observation hung', { mountHang: true }, 'renderer-timeout', 15000],
  ['state write failure', { stateReject: true }, 'window-or-state', 0],
]) test(`${name} exits nonzero and cleans windows/timers`, async t => {
  const h = harness(t, config); await flush(); await h.tick(delay)
  assert.ok(h.logs.some(r => r.reason === reason), JSON.stringify(h.logs))
  assert.deepEqual(h.app.exits, [1]); assert.ok(h.wins.every(w => w.dead)); assert.equal(h.jobs.size, 0)
  assert.equal(h.logs.some(r => JSON.stringify(r).includes('private')), false)
})

for (const event of ['did-fail-load', 'render-process-gone']) test(`${event} terminates bounded and ignores subframe load failures`, async t => {
  const h = harness(t); await flush(); const win = h.wins[0]
  win.webContents.emit('did-fail-load', {}, -3, 'private info', 'private url', false)
  assert.deepEqual(h.app.exits, [])
  if (event === 'did-fail-load') win.webContents.emit(event, {}, -7, 'private info', 'private url', true)
  else win.webContents.emit(event, {}, { reason: 'crashed' })
  assert.deepEqual(h.app.exits, [1]); assert.equal(win.dead, true); assert.equal(h.jobs.size, 0)
})

test('quit watchdog exits a hung app and cancels timers', async t => {
  const h = harness(t, { quitHang: true }); await flush(); h.control.quit()
  assert.ok(h.wins.every(w => w.dead)); await h.tick(2000)
  assert.deepEqual(h.app.exits, [1]); assert.equal(h.jobs.size, 0)
  assert.ok(h.logs.some(r => r.reason === 'quit-timeout'))
})

test('navigation, redirects, subframes, webviews and popups cannot escape owned entry', async t => {
  const h = harness(t); await flush(); const wc = h.wins[0].webContents
  assert.deepEqual(wc.popup(), { action: 'deny' })
  for (const name of ['will-navigate', 'will-redirect']) for (const [url, denied] of [['https://example.com', true], ['app://command-center/', true], ['app://overlay/assets/app.js', true], ['app://overlay/overlay.html', false]]) {
    let prevented = false; wc.emit(name, { preventDefault() { prevented = true } }, url); assert.equal(prevented, denied)
  }
  for (const [url, isMainFrame, denied] of [['app://overlay/', true, false], ['app://overlay/', false, true], ['https://example.com', true, true]]) {
    let prevented = false; wc.emit('will-frame-navigate', { url, isMainFrame, preventDefault() { prevented = true } }); assert.equal(prevented, denied)
  }
  let prevented = false; wc.emit('will-attach-webview', { preventDefault() { prevented = true } }); assert.equal(prevented, true)
})

test('IPC checks owned main-frame origin and strict payload types/bounds', async t => {
  const h = harness(t); await flush(); const win = h.wins[0], event = h.owned(win)
  assert.equal(h.handlers.get('mode:get')(event), 'OVERLAY_COMPACT')
  const strangers = [{ sender: {}, senderFrame: win.webContents.mainFrame }, { sender: win.webContents, senderFrame: { url: 'app://overlay/' } }]
  for (const stranger of strangers) {
    assert.equal(h.handlers.get('mode:get')(stranger), null)
    h.ipcMain.emit('overlay:move-to', stranger, { x: 1, y: 2 })
    h.ipcMain.emit('mode:request', stranger, 'COMMAND_CENTER_WINDOWED')
    h.ipcMain.emit('overlay:set-interactive', stranger, true)
  }
  for (const payload of [null, [], {}, { x: '1', y: 2 }, { x: NaN, y: 2 }, { x: 2, y: Infinity }, { x: 100001, y: 2 }]) h.ipcMain.emit('overlay:move-to', event, payload)
  for (const value of ['true', 1, {}, null]) h.ipcMain.emit('overlay:set-interactive', event, value)
  for (const value of [{}, null, 'INVALID', 'x'.repeat(100)]) h.ipcMain.emit('mode:request', event, value)
  assert.equal(h.wins.length, 1); assert.deepEqual(win.positions, []); assert.equal(win.ignore.length, 1)
  win.webContents.mainFrame.url = 'https://evil.test'
  assert.equal(h.handlers.get('mode:get')(event), null)
  h.ipcMain.emit('overlay:move-to', event, { x: 1, y: 2 }); assert.deepEqual(win.positions, [])
  win.webContents.mainFrame.url = 'app://overlay/'
  h.ipcMain.emit('overlay:move-to', event, { x: -100000, y: 20.4 }); assert.deepEqual(win.positions, [[-100000, 20]])
  h.ipcMain.emit('overlay:set-interactive', event, true); assert.deepEqual(win.ignore.at(-1), [false, undefined])
  h.ipcMain.emit('mode:request', event, 'COMMAND_CENTER_WINDOWED'); await flush(); assert.equal(h.wins.length, 2)
  const center = h.wins[1]
  h.ipcMain.emit('overlay:move-to', h.owned(center), { x: 9, y: 9 }); assert.equal(win.positions.length, 1)
  center.close(); assert.equal(center.dead, false); assert.equal(win.visible, true); assert.equal(h.app.quits, 0)
  win.close(); assert.equal(win.dead, false); assert.equal(win.visible, false)
  h.app.emit('second-instance'); assert.equal(win.visible, true, 'same-mode hidden overlay restores without a duplicate window')
})

test('overlay bounds, interaction transitions and lifecycle fail closed', async t => {
  const h = harness(t); await flush(); const win = h.wins[0], event = h.owned(win)
  assert.deepEqual(win.size, [420, 160])
  h.ipcMain.emit('mode:request', event, 'COMMAND_CENTER_FULLSCREEN')
  assert.equal(h.handlers.get('mode:get')(event), 'OVERLAY_COMPACT')
  h.ipcMain.emit('overlay:set-interactive', event, true)
  const count = win.ignore.length
  for (let i = 0; i < 1000; i++) h.ipcMain.emit('overlay:set-interactive', event, true)
  assert.equal(win.ignore.length, count)
  for (const name of ['blur', 'hide']) {
    win.emit(name); assert.deepEqual(win.ignore.at(-1), [true, { forward: true }])
    h.ipcMain.emit('overlay:set-interactive', event, true)
  }
  win.webContents.emit('did-start-loading')
  assert.deepEqual(win.ignore.at(-1), [true, { forward: true }])
  h.ipcMain.emit('mode:request', event, 'OVERLAY_EXPANDED'); await flush()
  assert.deepEqual(win.size, [420, 520])
  assert.deepEqual(win.webContents.sent.at(-1), ['mode:changed', 'OVERLAY_EXPANDED'])
  h.ipcMain.emit('mode:request', event, 'OVERLAY_COMPACT'); await flush()
  assert.deepEqual(win.size, [420, 160])
  h.control.requestMode('COMMAND_CENTER_WINDOWED'); await flush()
  const countHidden = win.ignore.length
  h.ipcMain.emit('overlay:set-interactive', event, true)
  h.ipcMain.emit('overlay:set-interactive', h.owned(h.wins[1]), true)
  h.ipcMain.emit('mode:request', event, 'OVERLAY_COMPACT')
  assert.equal(h.handlers.get('mode:get')(h.owned(h.wins[1])), 'COMMAND_CENTER_WINDOWED')
  assert.equal(win.ignore.length, countHidden)
  h.control.requestMode('OVERLAY_COMPACT'); await flush()
  h.ipcMain.emit('overlay:set-interactive', event, true)
  win.close(); assert.deepEqual(win.ignore.at(-1), [true, { forward: true }])
  h.ipcMain.emit('overlay:set-interactive', event, true)
  assert.deepEqual(win.ignore.at(-1), [true, { forward: true }])
  win.destroy(); h.ipcMain.emit('overlay:set-interactive', event, true)
  assert.equal(h.handlers.get('mode:get')(event), null)
  h.control.quit()
  h.ipcMain.emit('mode:request', h.owned(h.wins[1]), 'OVERLAY_COMPACT')
  assert.equal(h.handlers.get('mode:get')(h.owned(h.wins[1])), null)
  assert.ok(h.wins.every(w => w.dead))
})

test('late readiness after failure cannot resurrect a window', async t => {
  const h = harness(t, { loadHang: true }); await flush(); const win = h.wins[0]
  await h.tick(15000); win.webContents.emit('did-finish-load'); await flush()
  assert.equal(win.visible, false); assert.equal(h.jobs.size, 0); assert.equal(win.webContents.scripts.length, 0)
})

test('protocol failures after preflight exit nonzero and destroy every owned window', async t => {
  const { dist } = fixture(t)
  for (const failure of ['missing', 'unreadable', 'exception']) {
    const ui = createUiProtocol(dist)
    const h = harness(t, { ui }); await flush()
    if (failure === 'missing') fs.unlinkSync(path.join(dist, 'assets/app.js'))
    if (failure === 'unreadable') ui.respond = () => new Response('unreadable asset', { status: 500 })
    if (failure === 'exception') ui.respond = () => { throw new Error('private path') }
    const response = h.protocol.handler({ url: 'app://overlay/assets/app.js' })
    assert.equal(response.status, failure === 'missing' ? 404 : 500)
    assert.deepEqual(h.app.exits, [1]); assert.equal(h.jobs.size, 0); assert.ok(h.wins.every(w => w.dead))
    assert.ok(h.logs.some(r => r.reason === (failure === 'exception' ? 'ui-protocol-error' : 'ui-protocol-response')))
    fs.writeFileSync(path.join(dist, 'assets/app.js'), 'export const mounted = true')
  }
})

test('repeated mode requests reuse at most two windows and leave no polling timers', async t => {
  const h = harness(t); await flush()
  for (let i = 0; i < 100; i++) {
    assert.equal(h.control.requestMode('COMMAND_CENTER_WINDOWED'), true); await flush()
    assert.equal(h.control.requestMode('OVERLAY_COMPACT'), true); await flush()
  }
  assert.equal(h.wins.length, 2); assert.equal(h.jobs.size, 0)
  h.control.quit(); assert.ok(h.wins.every(w => w.dead)); assert.equal(h.jobs.size, 0)
})

test('smoke IPC cannot switch the selected entry or create another window', async t => {
  const h = harness(t, { smoke: { entry: 'overlay', state: path.resolve('unused-smoke-state') } }); await flush()
  h.ipcMain.emit('mode:request', h.owned(h.wins[0]), 'COMMAND_CENTER_WINDOWED'); await flush()
  assert.equal(h.wins.length, 1); assert.equal(h.handlers.get('mode:get')(h.owned(h.wins[0])), 'OVERLAY_COMPACT')
  await h.tick(1500); assert.equal(h.jobs.size, 0)
})

test('smoke state cannot create files through a symlinked ancestor', t => {
  const { dir } = fixture(t), root = path.join(dir, 'root'), outside = path.join(dir, 'outside')
  fs.mkdirSync(root); fs.mkdirSync(outside); fs.symlinkSync(outside, path.join(root, 'linked'))
  assert.throws(() => parseSmoke(['--smoke-entry=overlay', `--smoke-state=${path.join(root, 'linked', 'new-state')}`], root), /escape/)
  assert.equal(fs.existsSync(path.join(outside, 'new-state')), false)
})

test('native fullscreen notifications reconcile both renderers without re-entering on exit', async t => {
  const h = harness(t); await flush()
  h.control.requestMode('COMMAND_CENTER_WINDOWED'); await flush()
  const [overlay, center] = h.wins, event = h.owned(center)
  assert.equal(center.settings.frame, undefined, 'ordinary native frame retained')
  assert.equal(center.settings.resizable, undefined, 'ordinary native resize retained')
  center.nativeFullscreen(true)
  assert.equal(h.handlers.get('mode:get')(event), 'COMMAND_CENTER_FULLSCREEN')
  assert.deepEqual(center.fullscreenCalls, [], 'native entry did not request another entry')
  center.nativeFullscreen(false)
  center.emit('leave-full-screen')
  assert.equal(h.handlers.get('mode:get')(event), 'COMMAND_CENTER_WINDOWED')
  assert.deepEqual(center.fullscreenCalls, [], 'native exit never calls show/setFullScreen')
  for (const win of [overlay, center]) assert.deepEqual(win.webContents.sent.at(-1), ['mode:changed', 'COMMAND_CENTER_WINDOWED'])
  assert.equal(h.writes.at(-1)[1].lastActiveWorkspace, 'COMMAND_CENTER_WINDOWED')
  assert.equal(h.jobs.size, 0)
})

test('Escape exits only the visible contained Command Center on keyDown', async t => {
  const h = harness(t, { initialMode: 'COMMAND_CENTER_FULLSCREEN' }); await flush()
  const center = h.wins[0], event = h.owned(center)
  let prevented = 0
  const keyEvent = { preventDefault() { prevented++ } }
  for (const input of [null, {}, { type: 'keyUp', key: 'Escape' }, { type: 'keyDown', key: 'a' }]) center.webContents.emit('before-input-event', keyEvent, input)
  center.webContents.mainFrame.url = 'https://evil.test/'
  center.webContents.emit('before-input-event', keyEvent, { type: 'keyDown', key: 'Escape' })
  assert.equal(prevented, 0)
  center.webContents.mainFrame.url = 'app://command-center/'
  center.webContents.emit('before-input-event', keyEvent, { type: 'keyDown', key: 'Escape' })
  assert.equal(prevented, 1); assert.equal(center.isFullScreen(), false)
  assert.equal(h.handlers.get('mode:get')(event), 'COMMAND_CENTER_WINDOWED')
  center.webContents.emit('before-input-event', keyEvent, { type: 'keyDown', key: 'Escape' })
  assert.equal(prevented, 1, 'windowed Escape belongs to renderer')
  h.control.requestMode('OVERLAY_COMPACT'); await flush()
  center.webContents.emit('before-input-event', keyEvent, { type: 'keyDown', key: 'Escape' })
  h.wins[1].webContents.emit('before-input-event', keyEvent, { type: 'keyDown', key: 'Escape' })
  assert.equal(prevented, 1); assert.equal(h.wins[1].webContents.listenerCount('before-input-event'), 0)
})

test('normal geometry survives maximize/minimize/fullscreen and captures restored resize', async t => {
  const h = harness(t, { initialMode: 'COMMAND_CENTER_WINDOWED' }); await flush()
  const center = h.wins[0], box = { x: 100, y: 80, width: 1100, height: 720 }
  center.bounds = { ...box }; center.normalBounds = { ...box }; center.emit('resize')
  const storedBox = () => { const { x, y, width, height } = h.writes.at(-1)[1].commandCenter; return { x, y, width, height } }
  assert.deepEqual(storedBox(), box)
  center.maximize(); assert.deepEqual(storedBox(), box); assert.equal(h.writes.at(-1)[1].commandCenter.maximized, true)
  center.unmaximize(); assert.deepEqual(storedBox(), box); assert.equal(h.writes.at(-1)[1].commandCenter.maximized, false)
  const count = h.writes.length
  center.minimize(); assert.equal(h.writes.length, count)
  h.control.requestMode('COMMAND_CENTER_WINDOWED'); assert.equal(center.isMinimized(), false)
  h.control.requestMode('COMMAND_CENTER_FULLSCREEN'); assert.deepEqual(storedBox(), box)
  const fullscreenCount = h.writes.length
  center.emit('move'); center.emit('resize'); assert.equal(h.writes.length, fullscreenCount)
  center.nativeFullscreen(false); assert.deepEqual(storedBox(), box)
  const resized = { x: 90, y: 60, width: 960, height: 640 }
  center.bounds = { ...resized }; center.normalBounds = { ...resized }; center.emit('resize')
  assert.deepEqual(storedBox(), resized)
})

test('delayed entry cannot undo newer exit; stale native events do not mislabel mode', async t => {
  const h = harness(t, { initialMode: 'COMMAND_CENTER_WINDOWED', fullscreenDeferred: true }); await flush()
  const center = h.wins[0], event = h.owned(center)
  h.control.requestMode('COMMAND_CENTER_FULLSCREEN')
  h.control.requestMode('COMMAND_CENTER_WINDOWED')
  assert.equal(h.jobs.size, 1, 'one bounded pending transition')
  center.nativeFullscreen(true)
  assert.deepEqual(center.fullscreenCalls, [true, false, false], 'late entry requests exit, never entry')
  center.emit('leave-full-screen') // actual native state is still true
  assert.equal(h.handlers.get('mode:get')(event), 'COMMAND_CENTER_WINDOWED')
  center.nativeFullscreen(false)
  center.emit('enter-full-screen') // actual native state is false
  assert.equal(h.handlers.get('mode:get')(event), 'COMMAND_CENTER_WINDOWED')
  assert.equal(h.jobs.size, 0); assert.deepEqual(h.app.exits, [])
})

test('cancelled entry with no native event resolves at deadline instead of failing', async t => {
  const h = harness(t, { initialMode: 'COMMAND_CENTER_WINDOWED', fullscreenDeferred: true }); await flush()
  h.control.requestMode('COMMAND_CENTER_FULLSCREEN'); h.control.requestMode('COMMAND_CENTER_WINDOWED')
  await h.tick(2000)
  assert.deepEqual(h.app.exits, []); assert.equal(h.jobs.size, 0)
  assert.equal(h.handlers.get('mode:get')(h.owned(h.wins[0])), 'COMMAND_CENTER_WINDOWED')
})

test('close during deferred entry hides center immediately and rejects delayed fullscreen', async t => {
  const h = harness(t, { initialMode: 'COMMAND_CENTER_WINDOWED', fullscreenDeferred: true, fullscreenSurfacesWindow: true }); await flush()
  const center = h.wins[0]
  h.control.requestMode('COMMAND_CENTER_FULLSCREEN'); center.close(); await flush()
  const overlay = h.wins[1], event = h.owned(overlay)
  assert.equal(center.visible, false); assert.equal(center.dead, false); assert.equal(overlay.visible, true)
  center.nativeFullscreen(true); assert.equal(center.visible, false, 'late enter cannot surface hidden center')
  center.nativeFullscreen(false); assert.equal(center.visible, false, 'late leave cannot surface hidden center')
  assert.equal(h.handlers.get('mode:get')(event), 'OVERLAY_COMPACT')
  assert.deepEqual(overlay.ignore.at(-1), [true, { forward: true }])
  assert.equal(overlay.isFullScreen(), false); assert.deepEqual(overlay.fullscreenCalls, [])
  assert.equal(overlay.settings.frame, false); assert.equal(overlay.settings.alwaysOnTop, true)
  assert.equal(h.jobs.size, 0); assert.equal(h.app.quits, 0)
})

test('new entry supersedes pending exit and quit cancels native deadlines', async t => {
  const h = harness(t, { initialMode: 'COMMAND_CENTER_WINDOWED', fullscreenDeferred: true }); await flush()
  const center = h.wins[0]
  h.control.requestMode('COMMAND_CENTER_FULLSCREEN'); center.nativeFullscreen(true)
  h.control.requestMode('COMMAND_CENTER_WINDOWED'); h.control.requestMode('COMMAND_CENTER_FULLSCREEN')
  center.nativeFullscreen(true)
  assert.equal(h.handlers.get('mode:get')(h.owned(center)), 'COMMAND_CENTER_FULLSCREEN')
  assert.equal(h.jobs.size, 0)
  h.control.requestMode('COMMAND_CENTER_WINDOWED'); h.control.quit(); await h.tick(2000)
  center.emit('enter-full-screen'); center.emit('leave-full-screen')
  assert.ok(h.wins.every(win => win.dead)); assert.equal(h.jobs.size, 0); assert.deepEqual(h.app.exits, [])
})

for (const [config, delay, reason] of [
  [{ fullscreenDeferred: true }, 2000, 'fullscreen-timeout'],
  [{ fullscreenReject: true }, 0, 'fullscreen-transition'],
]) test(`${reason} fails closed with sanitized diagnostics and no windows/timers`, async t => {
  const h = harness(t, { ...config, initialMode: 'COMMAND_CENTER_WINDOWED' }); await flush()
  h.control.requestMode('COMMAND_CENTER_FULLSCREEN'); await h.tick(delay)
  assert.deepEqual(h.app.exits, [1]); assert.equal(h.jobs.size, 0); assert.ok(h.wins.every(win => win.dead))
  assert.ok(h.logs.some(r => r.reason === reason)); assert.equal(JSON.stringify(h.logs).includes('private'), false)
})

test('native mode persistence failure uses bounded shutdown', async t => {
  const config = { initialMode: 'COMMAND_CENTER_FULLSCREEN' }, h = harness(t, config); await flush()
  config.stateReject = true
  h.wins[0].nativeFullscreen(false)
  assert.deepEqual(h.app.exits, [1]); assert.equal(h.jobs.size, 0); assert.ok(h.wins.every(win => win.dead))
  assert.ok(h.logs.some(r => r.reason === 'window-or-state'))
})

test('restore-time state failure stops before touching the destroyed native window', async t => {
  const config = { initialMode: 'COMMAND_CENTER_WINDOWED' }, h = harness(t, config); await flush()
  h.wins[0].minimize(); config.stateReject = true
  assert.equal(h.control.requestMode('COMMAND_CENTER_WINDOWED'), false)
  assert.deepEqual(h.app.exits, [1]); assert.equal(h.jobs.size, 0); assert.ok(h.wins.every(win => win.dead))
  assert.ok(h.logs.some(r => r.reason === 'state-write'))
})

test('fullscreen roundtrips/close reuse windows and listeners; hostile center requests remain rejected', async t => {
  const h = harness(t); await flush()
  h.control.requestMode('COMMAND_CENTER_WINDOWED'); await flush()
  const [overlay, center] = h.wins, event = h.owned(center)
  const counts = [center.listenerCount('enter-full-screen'), center.listenerCount('leave-full-screen'), center.webContents.listenerCount('before-input-event')]
  for (let i = 0; i < 100; i++) {
    h.ipcMain.emit('mode:request', event, 'COMMAND_CENTER_FULLSCREEN')
    h.ipcMain.emit('mode:request', event, 'COMMAND_CENTER_FULLSCREEN')
    assert.equal(center.isFullScreen(), true)
    for (const target of [null, {}, 'INVALID', 'x'.repeat(100)]) h.ipcMain.emit('mode:request', event, target)
    h.ipcMain.emit('mode:request', { sender: center.webContents, senderFrame: { url: 'app://command-center/' } }, 'OVERLAY_COMPACT')
    assert.equal(h.handlers.get('mode:get')(event), 'COMMAND_CENTER_FULLSCREEN')
    center.close(); await flush()
    assert.equal(center.visible, false); assert.equal(center.isFullScreen(), false)
    assert.equal(overlay.visible, true); assert.deepEqual(overlay.ignore.at(-1), [true, { forward: true }])
    h.control.requestMode('COMMAND_CENTER_WINDOWED')
  }
  assert.equal(h.wins.length, 2); assert.equal(h.jobs.size, 0)
  assert.deepEqual([center.listenerCount('enter-full-screen'), center.listenerCount('leave-full-screen'), center.webContents.listenerCount('before-input-event')], counts)
  assert.equal(center.settings.alwaysOnTop, undefined)
  assert.deepEqual(overlay.fullscreenCalls, [])
})

test('smoke parsing selects only existing entries and new worktree-local isolated state', t => {
  const { dir } = fixture(t)
  assert.equal(parseSmoke([], dir), null)
  const state = path.join(dir, 'smoke-state')
  assert.deepEqual(parseSmoke(['--smoke-entry=overlay', `--smoke-state=${state}`], dir), { entry: 'overlay', state })
  for (const args of [['--smoke-entry=invalid', `--smoke-state=${path.join(dir, 'new')}`], ['--smoke-entry=overlay'], ['--smoke-entry=overlay', '--smoke-state=relative'], ['--smoke-entry=overlay', `--smoke-state=${state}`], ['--smoke-entry=overlay', `--smoke-state=${path.dirname(dir)}`], ['--smoke-entry=overlay', `--smoke-state=${dir}`, '--smoke-extra=bad']]) assert.throws(() => parseSmoke(args, dir))
})

// --- S05: mode invariants, display-aware restore and display-change recovery ---
const { MODES, TRANSITIONS } = require('./mode-manager')
const REACH = { HIDDEN: [], OVERLAY_COMPACT: ['OVERLAY_COMPACT'], OVERLAY_EXPANDED: ['OVERLAY_COMPACT', 'OVERLAY_EXPANDED'],
  COMMAND_CENTER_WINDOWED: ['COMMAND_CENTER_WINDOWED'], COMMAND_CENTER_FULLSCREEN: ['COMMAND_CENTER_WINDOWED', 'COMMAND_CENTER_FULLSCREEN'] }

function assertInvariants(h, mode) {
  const overlay = h.wins.find(w => w.settings.frame === false), center = h.wins.find(w => w.settings.frame !== false)
  assert.ok(h.wins.length <= 2, 'at most two windows')
  const overlayMode = mode.startsWith('OVERLAY_'), centerMode = mode.startsWith('COMMAND_CENTER')
  if (overlay) {
    assert.equal(overlay.visible, overlayMode, `${mode} overlay visibility`)
    if (!overlay.visible) assert.deepEqual(overlay.ignore.at(-1), [true, { forward: true }], `${mode} hidden overlay stays click-through`)
  } else assert.equal(overlayMode, false)
  if (center) {
    assert.equal(center.visible, centerMode, `${mode} center visibility`)
    assert.equal(center.fullscreen, mode === 'COMMAND_CENTER_FULLSCREEN', `${mode} fullscreen`)
  } else assert.equal(centerMode, false)
  if (mode === 'HIDDEN') assert.ok(h.wins.every(w => !w.visible))
}

for (const from of MODES) for (const target of MODES) {
  const allowed = from === target || TRANSITIONS[from].includes(target)
  test(`transition ${from} -> ${target} is ${allowed ? 'allowed and' : 'rejected and'} keeps window invariants`, async t => {
    const h = harness(t, { initialMode: 'HIDDEN' }); await flush()
    for (const step of REACH[from]) { assert.equal(h.control.requestMode(step), true); await flush() }
    assertInvariants(h, from)
    const wins = h.wins.length, writes = h.writes.length
    assert.equal(h.control.requestMode(target), allowed); await flush()
    if (allowed) assertInvariants(h, target)
    else { assertInvariants(h, from); assert.equal(h.wins.length, wins); assert.equal(h.writes.length, writes) }
    assert.deepEqual(h.app.exits, [])
  })
}

test('invalid or corrupt persisted state boots to the default overlay with a state-recovered log', async t => {
  for (const [rawState, reason] of [['{"initialMode":"BOGUS"}', 'invalid-initial-mode'], ['{not json', 'corrupt-file'], ['[]', 'corrupt-file']]) {
    const h = harness(t, { rawState }); await flush()
    assert.deepEqual(h.app.exits, [], rawState)
    assert.equal(h.handlers.get('mode:get')(h.owned(h.wins[0])), 'OVERLAY_COMPACT')
    assert.ok(h.logs.some(r => r.stage === 'state-recovered' && r.reason === reason), rawState)
    assert.equal(h.logs.some(r => r.reason === 'invalid-initial-mode' && r.stage === 'failure'), false)
  }
})

const second = { id: 2, workArea: { x: 1920, y: 0, width: 1920, height: 1080 } }
const centerAt = (x, y, extra = {}) => ({ ...store.DEFAULT_STATE, initialMode: 'COMMAND_CENTER_WINDOWED', commandCenter: { displayId: 2, x, y, width: 1280, height: 800, maximized: false, ...extra } })
const within = (b, a) => b.x >= a.x && b.y >= a.y && b.x + b.width <= a.x + a.width && b.y + b.height <= a.y + a.height

test('displayId is persisted for both windows and restored windows stay on their saved display', async t => {
  const h = harness(t, { displays: [primaryArea, second], state: centerAt(2100, 100) }); await flush()
  const center = h.wins[0]
  assert.deepEqual([center.bounds.x, center.bounds.y], [2100, 100])
  center.bounds = { x: 100, y: 100, width: 1280, height: 800 }; center.normalBounds = { ...center.bounds }; center.emit('move')
  assert.equal(h.writes.at(-1)[1].commandCenter.displayId, 1)
  h.control.requestMode('OVERLAY_COMPACT'); await flush()
  const overlay = h.wins[1]
  overlay.bounds = { x: 2000, y: 100, width: 420, height: 160 }; overlay.emit('move')
  assert.equal(h.writes.at(-1)[1].overlay.displayId, 2)
})

test('expanding the overlay near the bottom or right edge keeps it fully visible', async t => {
  const h = harness(t); await flush(); const overlay = h.wins[0]
  overlay.bounds = { x: 1800, y: 1000, width: 420, height: 160 }
  h.control.requestMode('OVERLAY_EXPANDED'); await flush()
  assert.deepEqual(overlay.bounds, { x: 1500, y: 560, width: 420, height: 520 })
  h.control.requestMode('OVERLAY_COMPACT'); await flush()
  assert.deepEqual(overlay.size, [420, 160]); assert.ok(within(overlay.bounds, primaryArea.workArea))
})

test('removing the display holding the windowed Command Center repositions and persists it', async t => {
  const h = harness(t, { displays: [primaryArea, second], state: centerAt(2100, 100) }); await flush()
  const center = h.wins[0]
  h.screen.displays = [primaryArea]; h.screen.emit('display-removed', {}, second)
  assert.ok(within(center.bounds, primaryArea.workArea), JSON.stringify(center.bounds))
  const saved = h.writes.at(-1)[1].commandCenter
  assert.equal(saved.displayId, 1); assert.deepEqual([saved.x, saved.y], [center.bounds.x, center.bounds.y])
  assert.ok(h.logs.some(r => r.stage === 'display-change' && r.event === 'display-removed' && r.repositioned.includes('command-center')))
  const count = h.writes.length
  h.screen.emit('display-added', second); assert.equal(h.writes.length, count, 'unchanged bounds are not rewritten')
})

test('removing the display holding the overlay moves it onto the primary display', async t => {
  const state = { ...store.DEFAULT_STATE, overlay: { displayId: 2, x: 2100, y: 100, width: 360, height: 72 } }
  const h = harness(t, { displays: [primaryArea, second], state }); await flush()
  const overlay = h.wins[0]
  assert.deepEqual([overlay.bounds.x, overlay.bounds.y], [2100, 100])
  h.screen.displays = [primaryArea]; h.screen.emit('display-removed', {}, second)
  assert.ok(within(overlay.bounds, primaryArea.workArea)); assert.equal(overlay.visible, true)
  assert.equal(h.writes.at(-1)[1].overlay.displayId, 1)
})

test('removing the display under a fullscreen Command Center ends windowed, visible and on the remaining display', async t => {
  const h = harness(t, { displays: [primaryArea, second], state: centerAt(2100, 100) }); await flush()
  const center = h.wins[0]
  h.control.requestMode('COMMAND_CENTER_FULLSCREEN'); assert.equal(center.fullscreen, true)
  h.screen.displays = [primaryArea]; h.screen.emit('display-removed', {}, second)
  assert.equal(h.handlers.get('mode:get')(h.owned(center)), 'COMMAND_CENTER_WINDOWED')
  assert.equal(center.fullscreen, false); assert.equal(center.visible, true)
  assert.ok(within(center.bounds, primaryArea.workArea), JSON.stringify(center.bounds))
  assert.equal(h.writes.at(-1)[1].commandCenter.displayId, 1)
})

test('a maximized Command Center on a removed display is unmaximized and re-resolved; one on a live display is left alone', async t => {
  const h = harness(t, { displays: [primaryArea, second], state: centerAt(2100, 100, { maximized: true }) }); await flush()
  const center = h.wins[0]; assert.equal(center.maximized, true)
  h.screen.emit('display-metrics-changed', second); assert.equal(center.maximized, true)
  h.screen.displays = [primaryArea]; h.screen.emit('display-removed', {}, second)
  assert.equal(center.maximized, false); assert.ok(within(center.bounds, primaryArea.workArea), JSON.stringify(center.bounds))
})

test('a metrics change that shrinks the work area clamps visible windows', async t => {
  const h = harness(t, { initialMode: 'COMMAND_CENTER_WINDOWED' }); await flush()
  const center = h.wins[0], small = { id: 1, workArea: { x: 0, y: 0, width: 1000, height: 600 } }
  h.screen.displays = [small]; h.screen.emit('display-metrics-changed', small)
  assert.ok(within(center.bounds, small.workArea), JSON.stringify(center.bounds))
  assert.ok(h.logs.some(r => r.stage === 'display-change' && r.event === 'display-metrics-changed'))
})

test('display events never touch hidden windows and a failing listener fails closed', async t => {
  const h = harness(t, { initialMode: 'COMMAND_CENTER_WINDOWED' }); await flush()
  h.control.requestMode('HIDDEN'); await flush()
  h.screen.displays = [{ id: 1, workArea: { x: 0, y: 0, width: 500, height: 400 } }]; h.screen.emit('display-metrics-changed')
  assert.deepEqual(h.logs.at(-1).repositioned, [])
  const bad = harness(t, { initialMode: 'COMMAND_CENTER_WINDOWED', displayThrows: false }); await flush()
  bad.screen.getAllDisplays = () => { throw new Error('private display failure') }
  bad.screen.emit('display-removed')
  assert.deepEqual(bad.app.exits, [1]); assert.ok(bad.logs.some(r => r.reason === 'display-change')); assert.equal(JSON.stringify(bad.logs).includes('private'), false)
})

test('quit removes the screen listeners and a screen without events still boots', async t => {
  const h = harness(t); await flush()
  const events = ['display-removed', 'display-added', 'display-metrics-changed']
  assert.deepEqual(events.map(e => h.screen.listenerCount(e)), [1, 1, 1])
  h.control.quit()
  assert.deepEqual(events.map(e => h.screen.listenerCount(e)), [0, 0, 0])
  const plain = harness(t, { plainScreen: true }); await flush()
  assert.deepEqual(plain.app.exits, []); assert.equal(plain.wins[0].visible, true)
})

test('core bridge IPC is owner-gated, allowlisted, fans out to live windows only and never leaks the token', async t => {
  const h = harness(t); await flush()
  const bridge = h.coreBridge, win = h.wins[0], event = h.owned(win)
  const stranger = { sender: new EventEmitter(), senderFrame: { url: 'app://overlay/' } }
  assert.equal(bridge.starts, 1)
  assert.equal(await h.handlers.get('core:get-state')(event), 'connecting')
  assert.equal(await h.handlers.get('core:get-state')(stranger), null)
  assert.equal(await h.handlers.get('core:get-state')(null), null)
  assert.deepEqual(await h.handlers.get('core:get-replay')(event), [{ v: 1, name: 'core.snapshot' }])
  assert.deepEqual(await h.handlers.get('core:get-replay')(stranger), [])
  assert.deepEqual(await h.handlers.get('core:get-replay')(null), [])
  const control = h.handlers.get('core:control')
  assert.deepEqual(await control(stranger, 'activate'), { ok: false, error: 'denied' })
  for (const bad of ['quit', 'restart', '', null, {}, 'x'.repeat(1000)]) assert.deepEqual(await control(event, bad), { ok: false, error: 'unsupported action' })
  assert.deepEqual(bridge.controls, [])
  assert.deepEqual(await control(event, 'activate'), { ok: true })
  assert.deepEqual(await control(event, 'sleep'), { ok: true })
  assert.deepEqual(bridge.controls, ['activate', 'sleep'])
  bridge.reject = true
  const failure = await control(event, 'activate')
  assert.deepEqual(failure, { ok: false, error: 'unauthorized' })
  assert.doesNotMatch(JSON.stringify(failure), /secret-token/)
  const dead = h.wins.length > 1 ? h.wins[1] : null
  assert.equal(dead, null)
  bridge.stateFns[0]('connected'); bridge.eventFns[0]({ v: 1, name: 'voice.state' })
  assert.deepEqual(win.webContents.sent.filter(([c]) => c.startsWith('core:')), [['core:state', 'connected'], ['core:event', { v: 1, name: 'voice.state' }]])
  win.destroy()
  bridge.stateFns[0]('reconnecting')
  assert.equal(win.webContents.sent.filter(([c]) => c === 'core:state').length, 1)
  assert.doesNotMatch(JSON.stringify(win.webContents.sent), /token/i)
})

test('core bridge stops on before-quit and failure and is never asked to quit Core', async t => {
  const h = harness(t); await flush()
  h.app.emit('before-quit')
  assert.equal(h.coreBridge.stops, 1)
  const failing = harness(t, { distReject: true }); await flush()
  assert.equal(failing.coreBridge.starts, 0)
  const loadFail = harness(t, { loadReject: true }); await flush()
  assert.ok(loadFail.coreBridge.stops >= 1)
})

test('JARVIS_CONTROL_PORT parsing accepts only integers 1-65535', () => {
  const { parseControlPort } = require('./main')
  assert.equal(parseControlPort('50000'), 50000)
  for (const bad of [undefined, '', '0', '65536', '-1', '1.5', 'abc', '80 ', '1e3', '999999']) assert.equal(parseControlPort(bad), 47811, String(bad))
})

// ---- S07/T04: isolated tray probe exercises real main-process behavior ----
for (const loginItem of [false, true]) test(`tray probe starts hidden with native controls, never reconciles login on startup (opt-in ${loginItem})`, async t => {
  const state = fs.mkdtempSync(path.resolve('tray-probe-fixture-'))
  t.after(() => fs.rmSync(state, { recursive: true, force: true }))
  const h = harness(t, { trayProbe: { state, loginItem }, state: { launchAtLogin: true }, loginInitially: true })
  await h.tick(0)
  assert.equal(h.app.paths.userData, state); assert.equal(h.app.paths.sessionData, state)
  assert.equal(h.wins.length, 0); assert.equal(h.native.trays.length, 1)
  assert.equal(h.native.registered.size, 2); assert.equal(h.native.loginSets.length, 0)
  assert.ok(h.logs.some(r => r.stage === 'tray-probe-ready' && r.mode === 'HIDDEN'))
  item(h, 'Launch at sign-in').click({ checked: false }); await h.tick(0)
  assert.equal(h.native.loginSets.length, loginItem ? 1 : 0)
  h.native.registered.get('Control+Alt+Shift+J')(); await h.tick(0)
  assert.ok(h.logs.some(r => r.stage === 'tray-probe-mode' && r.mode === 'OVERLAY_COMPACT'))
  assert.equal(h.wins[0].visible, true)
  assert.equal(h.wins[0].settings.webPreferences.sandbox, true)
  h.coreBridge.state = 'connected'; h.coreBridge.stateFns.forEach(fn => fn('connected'))
  h.native.dialogResponse = 1; item(h, 'Stop Jarvis Core…').click(); await h.tick(0)
  assert.equal(h.coreBridge.stopCalls, undefined)
})

// ---- S07/T02: tray, global shortcuts, login item, Exit-vs-Stop-Core ----
const item = (h, label) => h.native.templates.at(-1).find(i => i.label === label)
const itemLike = (h, re) => h.native.templates.at(-1).find(i => re.test(i.label || ''))

test('normal run creates the tray with tooltip and click-to-toggle overlay; menu rebuilds on mode and core state changes', async t => {
  const h = harness(t); await h.tick(0)
  const tray = h.native.trays[0]
  assert.equal(h.native.trays.length, 1); assert.equal(tray.tooltip, 'Jarvis'); assert.ok(h.stages().includes('tray-ready'))
  assert.equal(item(h, 'Hide overlay') !== undefined, true)
  tray.emit('click'); await h.tick(0)
  assert.equal(h.control && h.stages().filter(s => s === 'shown').length >= 1, true)
  assert.ok(item(h, 'Show overlay'))
  assert.equal(item(h, 'Stop Jarvis Core…').enabled, false)
  h.coreBridge.state = 'connected'; h.coreBridge.stateFns.forEach(fn => fn('connected')); await h.tick(0)
  assert.equal(item(h, 'Stop Jarvis Core…').enabled, true)
})

for (const [name, config] of [['smoke', { smoke: { entry: 'overlay', state: path.resolve('unused-smoke-state') } }], ['overlay probe', { probe: path.resolve('unused-overlay-probe') }], ['center probe', { centerProbe: path.resolve('unused-center-probe') }], ['window probe', { windowProbe: { state: path.resolve('unused-wp') } }], ['visual probe', { visualProbe: path.resolve('unused-visual-probe'), coreBridge: { start() { throw new Error('visual must not attach Core') } } }]]) test(`${name} mode never creates tray, shortcuts or login item`, async t => {
  const h = harness(t, config); await h.tick(0)
  if (name === 'visual probe') {
    for (const key of ['userData','sessionData','crashDumps']) assert.equal(h.app.paths[key], config.visualProbe)
    for (const mode of ['OVERLAY_COMPACT','OVERLAY_EXPANDED','COMMAND_CENTER_WINDOWED']) {
      assert.equal(h.control.requestMode(mode), true); await h.tick(0)
    }
    for (const win of h.wins) { win.emit('resize'); win.emit('move') }
    assert.equal(h.writes.length, 0, 'visual capture cannot persist state')
    assert.equal(h.stages().includes('bootstrap-ready'), false)
    assert.equal(h.stages().includes('failure'), false)
    const center = h.wins.find(w => w.settings.title === 'Jarvis command-center')
    assert.equal(await h.handlers.get('desktop:set-launch-at-login')(h.owned(center), true), null)
    assert.equal(await h.handlers.get('core:get-state')(h.owned(center)), null)
  }
  assert.equal(h.native.trays.length, 0); assert.equal(h.native.registerCalls.length, 0); assert.equal(h.native.loginSets.length, 0)
})

test('missing native APIs degrade silently (no tray, app keeps running)', async t => {
  const h = harness(t, { noNative: true }); await h.tick(0)
  assert.equal(h.stages().includes('failure'), false); assert.equal(h.app.quits, 0)
})

test('Exit desktop quits the app without stopping Core; Stop Core confirm sends one stop, cancel sends none', async t => {
  const h = harness(t); await h.tick(0)
  h.coreBridge.state = 'connected'; h.coreBridge.stateFns.forEach(fn => fn('connected')); await h.tick(0)
  const stop = item(h, 'Stop Jarvis Core…'), exit = item(h, 'Exit desktop (Jarvis keeps running)')
  assert.notEqual(stop.label, exit.label)
  h.native.dialogResponse = 0; stop.click(); await h.tick(0)
  assert.equal(h.coreBridge.stopCalls, undefined); assert.ok(h.stages().includes('core-stop-declined'))
  const dialogOpts = h.native.dialogs[0]
  assert.equal(dialogOpts.defaultId, 0); assert.equal(dialogOpts.buttons[0], 'Cancel')
  h.native.dialogResponse = 1; stop.click(); await h.tick(0)
  assert.equal(h.coreBridge.stopCalls, 1); assert.ok(h.stages().includes('core-stop-requested')); assert.equal(h.app.quits, 0)
  exit.click(); await h.tick(0)
  assert.equal(h.coreBridge.stopCalls, 1); assert.equal(h.app.quits, 1); assert.equal(h.coreBridge.stops, 1)
})

test('Stop Core failure is logged with a typed reason and the desktop keeps running', async t => {
  const h = harness(t); await h.tick(0)
  h.coreBridge.stopReject = true; h.native.dialogResponse = 1
  h.coreBridge.state = 'connected'; h.coreBridge.stateFns.forEach(fn => fn('connected'))
  item(h, 'Stop Jarvis Core…').click(); await h.tick(0)
  const failure = h.logs.find(r => r.stage === 'core-stop-failed')
  assert.equal(failure.reason, 'unreachable'); assert.equal(h.app.quits, 0)
})

test('default shortcuts register and drive mode transitions', async t => {
  const h = harness(t); await h.tick(0)
  const { DEFAULT_SHORTCUTS } = require('./shortcuts')
  assert.deepEqual([...h.native.registered.keys()], Object.values(DEFAULT_SHORTCUTS))
  assert.equal(h.logs.filter(r => r.stage === 'shortcut-registered').length, 2)
  const overlay = h.native.registered.get(DEFAULT_SHORTCUTS.toggleOverlay), center = h.native.registered.get(DEFAULT_SHORTCUTS.toggleCommandCenter)
  const mode = () => h.native.templates.at(-1) && (item(h, 'Hide overlay') ? 'overlay' : 'other')
  overlay(); await h.tick(0); assert.equal(item(h, 'Show overlay') !== undefined, true) // overlay -> HIDDEN
  overlay(); await h.tick(0); assert.equal(mode(), 'overlay') // HIDDEN -> overlay
  center(); await h.tick(0); assert.equal(h.wins.some(w => w.settings.title === 'Jarvis command-center' && w.visible), true)
  center(); await h.tick(0); assert.equal(mode(), 'overlay') // center -> overlay
  center(); await h.tick(0)
  overlay(); await h.tick(0); assert.equal(mode(), 'overlay') // from command center, overlay shortcut returns to overlay
})

test('shortcut collision (register false) or throw logs shortcut-unavailable, marks the menu and never fails the app', async t => {
  const { DEFAULT_SHORTCUTS } = require('./shortcuts')
  const h = harness(t, { shortcutBlocked: [DEFAULT_SHORTCUTS.toggleOverlay] }); await h.tick(0)
  const unavailable = h.logs.find(r => r.stage === 'shortcut-unavailable')
  assert.deepEqual({ action: unavailable.action, accelerator: unavailable.accelerator, reason: unavailable.reason }, { action: 'toggleOverlay', accelerator: DEFAULT_SHORTCUTS.toggleOverlay, reason: 'registration-failed' })
  assert.match(itemLike(h, /^Toggle overlay/).label, /\(unavailable\)$/)
  assert.equal(itemLike(h, /^Toggle Command Center/).label.includes('unavailable'), false)
  assert.equal(h.stages().includes('failure'), false); assert.equal(h.app.quits, 0)
  const t2 = harness(t, { shortcutThrows: true }); await t2.tick(0)
  assert.equal(t2.logs.filter(r => r.stage === 'shortcut-unavailable' && r.reason === 'register-threw').length, 2)
  assert.equal(t2.stages().includes('failure'), false)
})

test('shortcut rejections from persisted state are logged and defaults still register', async t => {
  const h = harness(t, { rawState: JSON.stringify({ shortcuts: { toggleOverlay: 'Control+Shift+P' } }) }); await h.tick(0)
  const rejected = h.logs.find(r => r.stage === 'shortcut-rejected')
  assert.equal(rejected.action, 'toggleOverlay'); assert.equal(rejected.reason, 'reserved')
  assert.equal(h.native.registered.size, 2)
})

test('launch-at-sign-in defaults off and never enables the OS login item; enabling persists, applies and stays truthful', async t => {
  const h = harness(t); await h.tick(0)
  assert.equal(h.native.loginSets.length, 0)
  assert.equal(item(h, 'Launch at sign-in').checked, false)
  item(h, 'Launch at sign-in').click({ checked: true }); await h.tick(0)
  assert.equal(h.native.loginSets.length, 1)
  assert.equal(h.native.loginSets[0].openAtLogin, true); assert.equal(h.native.loginSets[0].args.at(-1), '--launched-at-login')
  assert.equal(h.writes.at(-1)[1].launchAtLogin, true); assert.ok(h.stages().includes('login-item-applied'))
  assert.equal(item(h, 'Launch at sign-in').checked, true)
  item(h, 'Launch at sign-in').click({ checked: false }); await h.tick(0)
  assert.equal(h.native.loginSets.at(-1).openAtLogin, false); assert.equal(h.writes.at(-1)[1].launchAtLogin, false)
})

test('persisted launchAtLogin=true is reconciled to the OS on startup; OS refusal is reported and persisted truthfully', async t => {
  const h = harness(t, { state: { launchAtLogin: true } }); await h.tick(0)
  assert.equal(h.native.loginSets.length, 1); assert.equal(h.native.loginSets[0].openAtLogin, true)
  const refusing = harness(t); await refusing.tick(0)
  refusing.app.setLoginItemSettings = () => { throw new Error('denied') }
  item(refusing, 'Launch at sign-in').click({ checked: true }); await refusing.tick(0)
  assert.ok(refusing.logs.some(r => r.stage === 'login-item-failed'))
  assert.equal(refusing.stages().includes('failure'), false)
})

test('login item startup failure reconciles persisted state with the OS and does not quit', async t => {
  const h = harness(t, { state: { launchAtLogin: true }, loginReject: true }); await h.tick(0)
  assert.equal(h.writes.at(-1)[1].launchAtLogin, false)
  assert.equal(item(h, 'Launch at sign-in').checked, false)
  assert.ok(h.stages().includes('login-item-failed'))
  assert.equal(h.app.quits, 0)
  const previous = harness(t, { loginInitially: true }); await previous.tick(0)
  assert.equal(previous.native.loginSets[0].openAtLogin, false)
})

test('Stop Core coalesces repeated clicks, treats dialog failure as cancel, and reports bridge failure without quitting', async t => {
  const h = harness(t); await h.tick(0)
  h.coreBridge.state = 'connected'; h.coreBridge.stateFns.forEach(fn => fn('connected'))
  const stop = item(h, 'Stop Jarvis Core…')
  h.native.dialogResponse = 1
  stop.click(); stop.click(); await h.tick(0)
  assert.equal(h.native.dialogs.length, 1)
  assert.equal(h.coreBridge.stopCalls, 1)
  h.coreBridge.stopReject = true
  stop.click(); await h.tick(0)
  assert.ok(h.logs.some(r => r.stage === 'core-stop-failed' && r.reason === 'unreachable'))
  h.native.dialogReject = true
  stop.click(); await h.tick(0)
  assert.equal(h.coreBridge.stopCalls, 2)
  assert.ok(h.stages().includes('core-stop-declined'))
  assert.equal(h.app.quits, 0)
  h.coreBridge.state = 'disconnected'; h.coreBridge.stateFns.forEach(fn => fn('disconnected'))
  assert.equal(item(h, 'Stop Jarvis Core…').enabled, false)
  stop.click(); await h.tick(0)
  assert.equal(h.coreBridge.stopCalls, 2)
})

test('--launched-at-login starts HIDDEN (tray only)', async t => {
  process.argv.push('--launched-at-login')
  t.after(() => process.argv.splice(process.argv.indexOf('--launched-at-login'), 1))
  const h = harness(t); await h.tick(0)
  assert.equal(h.wins.length, 0); assert.equal(h.native.trays.length, 1); assert.ok(item(h, 'Show overlay'))
})

test('Settings tray item opens Command Center and notifies only that window once mounted', async t => {
  const h = harness(t); await h.tick(0)
  item(h, 'Settings').click(); await h.tick(0)
  const center = h.wins.find(w => w.settings.title === 'Jarvis command-center')
  assert.equal(center.webContents.sent.filter(a => a[0] === 'desktop:open-settings').length, 1)
  assert.equal(h.wins.filter(w => w !== center).some(w => w.webContents.sent.some(a => a[0] === 'desktop:open-settings')), false)
})

test('quit unregisters every global shortcut and destroys the tray exactly once; repeated quit is harmless', async t => {
  const h = harness(t); await h.tick(0)
  h.control.quit(); h.control.quit(); await h.tick(0)
  assert.equal(h.native.unregisterAll, 1); assert.equal(h.native.trays[0].destroyed, 1)
  const b = harness(t); await b.tick(0)
  b.app.emit('before-quit'); b.app.emit('before-quit'); await b.tick(0)
  assert.equal(b.native.unregisterAll, 1); assert.equal(b.native.trays[0].destroyed, 1)
})

test('renderer IPC exposes no Core quit/stop path', async t => {
  const h = harness(t); await h.tick(0)
  const channels = [...h.handlers.keys(), ...h.ipcMain.eventNames().filter(n => typeof n === 'string')]
  assert.equal(channels.some(c => /quit|stop|exit|shortcut/i.test(c)), false)
  const win = h.wins[0]
  assert.deepEqual(await h.handlers.get('core:control')(h.owned(win), 'quit'), { ok: false, error: 'unsupported action' })
  assert.equal(h.coreBridge.stopCalls, undefined)
})
