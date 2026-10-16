'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { parseRoute, allowedNavigation, CSP } = require('./ui-protocol')
const { startDesktop } = require('./main')

const read = name => fs.readFileSync(path.join(__dirname, name), 'utf8')
const main = read('main.js')

test('every BrowserWindow isolates the renderer', () => {
  assert.equal((main.match(/new BrowserWindow\(/g) || []).length, 1)
  const prefs = /webPreferences: \{([^}]*)\}/.exec(main)[1]
  for (const flag of ['contextIsolation: true', 'nodeIntegration: false', 'sandbox: true']) assert.ok(prefs.includes(flag), flag)
  assert.match(prefs, /devTools: process\.env\.JARVIS_DESKTOP_DEVTOOLS === '1'/)
  assert.doesNotMatch(main, /webviewTag|nodeIntegrationInWorker|nodeIntegrationInSubFrames|webSecurity: false|allowRunningInsecureContent|enableRemoteModule/)
  for (const guard of ["setWindowOpenHandler(() => ({ action: 'deny' }))", "'will-navigate', 'will-redirect'", "'will-frame-navigate'", "'will-attach-webview'"]) assert.ok(main.includes(guard), guard)
})

test('CSP forbids eval, remote scripts, plugins and frames', () => {
  const directives = Object.fromEntries(CSP.split(';').map(d => d.trim().split(/\s+/)).map(([k, ...v]) => [k, v]))
  assert.doesNotMatch(CSP, /unsafe-eval/)
  assert.deepEqual(directives['script-src'], ["'self'"])
  assert.deepEqual(directives['default-src'], ["'self'"])
  assert.deepEqual(directives['object-src'], ["'none'"])
  assert.deepEqual(directives['frame-src'], ["'none'"])
  assert.deepEqual(directives['base-uri'], ["'none'"])
  for (const source of directives['connect-src']) assert.ok(source === "'self'" || /^(ws|http):\/\/127\.0\.0\.1:\*$/.test(source), source)
})

test('parseRoute rejects traversal, encoded, absolute and foreign paths', () => {
  const bad = ['app://overlay/../secret', 'app://overlay/assets/../../x', 'app://overlay/%2e%2e/x', 'app://overlay/assets/%2fx.js', 'app://overlay/%5cwindows',
    'app://overlay/C:/Windows/win.ini', 'app://overlay//etc/passwd', 'app://overlay/assets\\x.js', 'app://evil/index.html', 'file:///etc/passwd',
    'https://example.com/', 'app://overlay/secret.txt', 'app://overlay/assets/page.html', 'app://overlay/assets/%zz.js', 'app://overlay/a b', 'app://overlay/assets/x.js.', 'app://overlay/assets/a\u0000.js']
  for (const url of bad) assert.throws(() => parseRoute(url), url)
  assert.deepEqual(parseRoute('app://overlay/'), { host: 'overlay', file: 'overlay.html' })
  assert.equal(allowedNavigation('app://overlay/', 'command-center'), false)
  assert.equal(allowedNavigation('https://evil.test/', 'overlay'), false)
})

test('preload exposes only the jarvisDesktop bridge without raw IPC or require', () => {
  const exposed = []
  const ipc = new EventEmitter()
  ipc.invoke = async () => null; ipc.send = () => {}
  const context = { require: id => { assert.equal(id, 'electron'); return { contextBridge: { exposeInMainWorld: (name, value) => exposed.push([name, value]) }, ipcRenderer: ipc } } }
  vm.runInNewContext(read('preload.js'), context)
  assert.equal(exposed.length, 1)
  const [[name, api]] = exposed
  assert.equal(name, 'jarvisDesktop')
  assert.deepEqual(Object.keys(api).sort(), ['core', 'getMode', 'moveOverlayTo', 'onModeChanged', 'requestMode', 'setOverlayInteractive', 'settings'])
  assert.deepEqual(Object.keys(api.core).sort(), ['control', 'getReplay', 'getState', 'onEvent', 'onState'])
  assert.deepEqual(Object.keys(api.settings).sort(), ['get', 'onChanged', 'onOpenRequested', 'setLaunchAtLogin'])
  const walk = (value, seen = new Set()) => {
    for (const [key, item] of Object.entries(value)) {
      assert.doesNotMatch(key, /ipcRenderer|require|shell|exec|spawn|fs|process/i)
      assert.notEqual(item, ipc)
      if (item && typeof item === 'object' && !seen.has(item)) { seen.add(item); walk(item, seen) }
    }
  }
  walk(api)
  assert.doesNotMatch(read('preload.js'), /exposeInMainWorld\((?!'jarvisDesktop')/)
})

test('every ipcMain handler is gated by owned() sender authentication', () => {
  const registrations = [...main.matchAll(/ipcMain\.(handle|on)\('([^']+)'/g)]
  assert.deepEqual(registrations.map(m => m[2]).sort(), ['core:control', 'core:get-replay', 'core:get-state', 'desktop:get-settings', 'desktop:set-launch-at-login', 'mode:get', 'mode:request', 'overlay:move-to', 'overlay:set-interactive'])
  const body = main.slice(main.indexOf('function registerIpc()'), main.indexOf('function startCoreBridge()'))
  const chunks = body.split(/\n    (?=ipcMain\.)/).slice(1)
  assert.equal(chunks.length, registrations.length)
  for (const chunk of chunks) assert.match(chunk, /owned\(event/, chunk.split('\n')[0])
  assert.doesNotMatch(main, /ipcMain\.(handleOnce|once)\(|ipcMain\.on\('(?!mode:request|overlay:)/)
})

test('desktop sources never import process, shell or filesystem-escape modules', () => {
  for (const name of ['main.js', 'preload.js', 'core-bridge.js', 'ui-protocol.js']) {
    const source = read(name)
    assert.doesNotMatch(source, /require\(['"](?:node:)?(?:child_process|vm|worker_threads|cluster)['"]\)/, name)
    assert.doesNotMatch(source, /\bshell\b\s*[.}]|\bshell\s*[,}]|shell\.(openExternal|openPath|showItemInFolder)/, name)
    assert.doesNotMatch(source, /\beval\(|new Function\(|\.openDevTools\(|remote\.require|enableBlinkFeatures/, name)
  }
  assert.doesNotMatch(read('preload.js'), /require\((?!'electron')/)
  assert.doesNotMatch(read('core-bridge.js'), /\.spawn\(|\bexec(?:Sync|File)\(/)
})

test('permission requests and checks are denied for the default session', () => {
  assert.match(main, /setPermissionRequestHandler\(\(_contents, permission, callback\) => \{[^\n]*callback\(false\)/)
  assert.match(main, /setPermissionCheckHandler\(\(\) => false\)/)
  assert.ok(main.indexOf('denyPermissions()\n      registerIpc()') > 0, 'handlers are installed before any window exists')
})

test('web-contents-created baseline denies popups, webviews and foreign navigation', () => {
  const app = new EventEmitter(), logs = []
  Object.assign(app, { getPath: () => __dirname, commandLine: { appendSwitch() {} }, disableHardwareAcceleration() {}, requestSingleInstanceLock: () => true, whenReady: () => new Promise(() => {}), quit() {}, exit() {} })
  startDesktop({ app, protocol: { registerSchemesAsPrivileged() {} } }, { log: record => logs.push(record.stage), probe: null, smoke: null, centerProbe: null, windowProbe: null, trayProbe: null, timers: { setTimeout: () => 0, clearTimeout() {} } })
  const contents = new EventEmitter()
  let opener
  contents.setWindowOpenHandler = fn => { opener = fn }
  app.emit('web-contents-created', {}, contents)
  assert.deepEqual(opener({ url: 'https://evil.test' }), { action: 'deny' })
  const attach = { prevented: false, preventDefault() { this.prevented = true } }
  contents.emit('will-attach-webview', attach); assert.equal(attach.prevented, true)
  for (const [eventName, url, blocked] of [['will-navigate', 'https://evil.test/', true], ['will-redirect', 'file:///C:/x', true], ['will-navigate', 'app://overlay/', false]]) {
    const event = { prevented: false, preventDefault() { this.prevented = true } }
    contents.emit(eventName, event, url)
    assert.equal(event.prevented, blocked, url)
  }
  assert.deepEqual(logs.filter(s => /denied/.test(s)), ['window-open-denied', 'webview-denied', 'navigation-denied', 'navigation-denied'])
})

test('core bridge is attach-only on loopback', () => {
  const source = read('core-bridge.js')
  assert.match(source, /const HOST = '127\.0\.0\.1'/)
  assert.doesNotMatch(source, /host:\s+(?!HOST\b)\S/)
})
