'use strict'

const path = require('node:path')
const fs = require('node:fs')
const { createModeManager, MODES } = require('./mode-manager')
const stateStore = require('./window-state-store')
const { createUiProtocol, allowedNavigation, ENTRIES } = require('./ui-protocol')
const { parseProbe, createProbe } = require('./overlay-probe')
const commandProbe = require('./command-center-probe')
const nativeVisualProbe = require('./visual-probe')
const windowStateProbe = require('./window-state-probe')
const nativeTrayProbe = require('./tray-probe')
const { createCoreBridge } = require('./core-bridge')
const { SHORTCUT_ACTIONS } = require('./shortcuts')
const { buildTrayMenu, trayIcon } = require('./tray')
const ROOT = path.resolve(__dirname, '..', '..')
const UI_DIST = path.join(ROOT, 'ui', 'dist')
// Fixed, main-process-only observation. No renderer shutdown/eval capability.
// Same visible-region rule as ui/src/shell/overlay-interop.ts; fixed, numeric inputs only.
const INTERACTIVE_AT = `(x, y) => {
  const region = document.elementFromPoint(x, y)?.closest('[data-overlay-interactive]')
  return !document.hidden && Boolean(region?.isConnected && region.getClientRects().length &&
    !region.closest('[hidden], [inert], [aria-hidden="true"]') && getComputedStyle(region).visibility === 'visible')
}`
const CORE_ACTIONS = ['activate', 'sleep']
const MOUNT_CHECK ="Boolean(document.getElementById('root')?.childElementCount)"

function parseControlPort(value) {
  const port = /^\d{1,5}$/.test(String(value ?? '')) ? Number(value) : NaN
  return port >= 1 && port <= 65535 ? port : 47811
}

function parseSmoke(argv, root = ROOT) {
  const args = argv.filter(arg => arg.startsWith('--smoke-'))
  if (!args.length) return null
  if (args.length !== 2) throw new Error('smoke-arguments')
  const entry = args.find(arg => arg.startsWith('--smoke-entry='))?.slice(14)
  const state = args.find(arg => arg.startsWith('--smoke-state='))?.slice(14)
  const rel = state && path.relative(root, state)
  if (!Object.hasOwn(ENTRIES, entry) || !state || !path.isAbsolute(state) || !rel || rel === '..' || rel.startsWith(`..${path.sep}`) || path.isAbsolute(rel)) throw new Error('smoke-arguments')
  // The harness owns this otherwise-unused directory. Refuse existing paths
  // (including symlinks), so smoke cannot overwrite real preferences.
  if (fs.existsSync(state)) throw new Error('smoke-state-exists')
  const realRoot = fs.realpathSync(root)
  let ancestor = path.dirname(state)
  while (!fs.existsSync(ancestor)) ancestor = path.dirname(ancestor)
  const ancestorRel = path.relative(realRoot, fs.realpathSync(ancestor))
  if (ancestorRel === '..' || ancestorRel.startsWith(`..${path.sep}`) || path.isAbsolute(ancestorRel)) throw new Error('smoke-state-escape')
  fs.mkdirSync(state, { recursive: true })
  const realRel = path.relative(realRoot, fs.realpathSync(state))
  if (!realRel || realRel === '..' || realRel.startsWith(`..${path.sep}`) || path.isAbsolute(realRel)) throw new Error('smoke-state-escape')
  return { entry, state }
}

// Test seam wraps the actual production bootstrap; no mock behavior at runtime.
function startDesktop(electron, options = {}) {
  const { app, BrowserWindow, ipcMain, screen, protocol, Tray, Menu, globalShortcut, dialog, nativeImage } = electron
  const store = options.stateStore || stateStore
  const ui = options.ui || createUiProtocol(UI_DIST)
  const timers = options.timers || { setTimeout, clearTimeout }
  const log = options.log || (record => console.log(`[jarvis-bootstrap] ${JSON.stringify(record)}`))
  const pending = new Set()
  const windows = new Map()
  const mounted = new Set()
  const requested = new Set()
  const fullscreenPending = new WeakMap()
  let modeManager, persistedState, stateFile, smoke, probe, centerProbe, windowProbe, trayProbe, visualProbe
  let centerController = false, windowController = false, visualController = false
  let stopping = false
  let failed = false
  let quitTimer, coreBridge, tray, nativeUp = false, openSettingsPending = false, coreStopPending = false
  const shortcutStatus = {}
  const displayListeners = []
  // Stopping the bridge only closes this app's own sockets; Core is never signalled.
  function stopBridge() { try { coreBridge?.stop() } catch { /* shutdown must not throw */ } }
  function broadcast(channel, payload) {
    if (stopping) return
    for (const win of windows.values()) if (!win.isDestroyed() && !win.webContents.isDestroyed?.()) win.webContents.send(channel, payload)
  }
  const mark = (stage, fields = {}) => log({ stage, pid: process.pid, ...fields })
  function later(fn, ms) {
    const id = timers.setTimeout(() => { pending.delete(id); fn() }, ms)
    pending.add(id)
    return id
  }
  function cancel(id) { if (id !== undefined) { timers.clearTimeout(id); pending.delete(id) } }
  function clearTimers() { for (const id of pending) timers.clearTimeout(id); pending.clear() }
  function unlistenDisplays() {
    if (typeof screen.removeListener !== 'function') return
    for (const [event, listener] of displayListeners) screen.removeListener(event, listener)
    displayListeners.length = 0
  }
  // Tray and OS-global shortcuts outlive windows; release them on every exit path.
  function releaseNative() {
    if (!nativeUp) return
    nativeUp = false
    try { globalShortcut?.unregisterAll() } catch { /* shutdown must not throw */ }
    try { tray?.destroy() } catch { /* shutdown must not throw */ }
    tray = null
  }
  function quit(code = 0) {
    if (stopping) return
    stopping = true
    clearTimers()
    unlistenDisplays()
    stopBridge()
    releaseNative()
    mark('closing', { code })
    for (const win of windows.values()) if (!win.isDestroyed()) win.destroy()
    mark('closed', { code })
    // An explicit quit is distinct from normal close-to-mode/hide behavior.
    quitTimer = later(() => { mark('failure', { reason: 'quit-timeout' }); app.exit(code || 1) }, 2000)
    if (code) app.exit(code)
    else app.quit()
  }
  function fail(reason, fields = {}) {
    if (stopping) return
    failed = true
    mark('failure', { reason, ...fields })
    quit(1)
  }
  function displayIdFor(bounds) {
    const match = typeof screen.getDisplayMatching === 'function' ? screen.getDisplayMatching(bounds) : null
    return match?.id ?? store.displayForBounds(bounds, screen.getAllDisplays())
  }
  function persist(host, win) {
    if (stopping || smoke || probe || centerProbe || visualProbe || win.isDestroyed()) return
    const key = host === 'overlay' ? 'overlay' : 'commandCenter'
    if (host === 'command-center') {
      // Native transition/resize events can precede the final geometry. Never
      // replace ordinary bounds with minimized, maximized or fullscreen bounds.
      if (!requested.has(host) || win.isMinimized() || win.isFullScreen() || fullscreenPending.has(win) || modeManager.getMode() === 'COMMAND_CENTER_FULLSCREEN') return
      const normal = win.getNormalBounds()
      persistedState[key] = { ...persistedState[key], ...(!win.isMaximized() ? normal : {}), displayId: displayIdFor(normal), maximized: win.isMaximized() }
    } else {
      const bounds = win.getBounds()
      persistedState[key] = { ...persistedState[key], ...bounds, displayId: displayIdFor(bounds) }
    }
    try { store.saveState(stateFile, persistedState) } catch { fail('state-write') }
  }
  const interaction = new WeakMap()
  function setInteractive(win, value) {
    if (win.isDestroyed() || interaction.get(win) === value) return
    interaction.set(win, value)
    win.setIgnoreMouseEvents(!value, value ? undefined : { forward: true })
    mark('overlay-interaction', { interactive: value })
  }
  // Electron's forward:true mouse forwarding is unreliable on Windows (injected or
  // fast input never reaches a click-through window), so poll the cursor instead.
  let tracking = false
  function trackCursor(win) {
    if (tracking || typeof screen.getCursorScreenPoint !== 'function') return
    tracking = true
    async function tick() {
      if (stopping || win.isDestroyed()) { tracking = false; return }
      try {
        if (win.isVisible() && modeManager.getMode().startsWith('OVERLAY_')) {
          const point = screen.getCursorScreenPoint(), box = win.getContentBounds()
          const x = Math.round(point.x - box.x), y = Math.round(point.y - box.y)
          if (x < 0 || y < 0 || x >= box.width || y >= box.height) setInteractive(win, false)
          else setInteractive(win, (await win.webContents.executeJavaScript(`(${INTERACTIVE_AT})(${x}, ${y})`)) === true)
        }
      } catch { /* window closing or renderer reloading: next tick decides */ }
      later(tick, 16)
    }
    later(tick, 16)
  }
  function notifyMode() {
    for (const win of windows.values()) if (!win.isDestroyed()) win.webContents.send('mode:changed', modeManager.getMode())
  }
  function setFullscreen(win, target) {
    const previous = fullscreenPending.get(win)
    if (previous?.target === target) return
    if (!previous && win.isFullScreen() === target) return
    cancel(previous?.timer)
    const transition = { target, timer: later(() => {
      // Cancellation can leave the native window already at the requested state
      // without Electron emitting another event (e.g. exit before enter commits).
      if (!win.isDestroyed() && win.isFullScreen() === target) reconcileFullscreen(win, target)
      else fail('fullscreen-timeout', { entry: 'command-center' })
    }, 2000) }
    fullscreenPending.set(win, transition)
    try { win.setFullScreen(target) } catch { fail('fullscreen-transition', { entry: 'command-center' }) }
  }
  function reconcileFullscreen(win, entered) {
    if (stopping || win.isDestroyed() || win.isFullScreen() !== entered) return // stale/out-of-order notification
    const transition = fullscreenPending.get(win)
    // Some native transitions can surface a window again after hide(). A stale
    // Command Center must remain hidden on both enter and leave notifications.
    if (!requested.has('command-center')) win.hide()
    // A delayed enter cannot undo a newer return/exit request. Keep the original
    // deadline so a failing OS transition cannot spin or wait indefinitely.
    if (entered && (transition?.target === false || !requested.has('command-center'))) {
      if (!transition) setFullscreen(win, false)
      else try { win.setFullScreen(false) } catch { fail('fullscreen-transition', { entry: 'command-center' }) }
      return
    }
    cancel(transition?.timer)
    fullscreenPending.delete(win)
    if (!requested.has('command-center')) return
    const target = entered ? 'COMMAND_CENTER_FULLSCREEN' : 'COMMAND_CENTER_WINDOWED'
    // Reconcile state only: calling show/requestMode here would re-enter
    // fullscreen after a native exit, or recursively initiate another transition.
    try {
      if (modeManager.getMode() !== target && modeManager.canTransition(target)) {
        modeManager.beginTransition(target)()
        if (!smoke && !probe && !centerProbe && !visualProbe) { persistedState.lastActiveWorkspace = target; store.saveState(stateFile, persistedState) }
      }
      mark('native-fullscreen', { fullscreen: entered })
      notifyMode()
    } catch { fail('window-or-state') }
  }
  function show(host, win) {
    if (stopping || win.isDestroyed() || !mounted.has(host) || !requested.has(host)) return
    win.show()
    if (stopping || win.isDestroyed()) return
    if (host === 'overlay') {
      setInteractive(win, false)
      trackCursor(win)
      const at = win.getBounds()
      const { x, y, width, height } = store.resolveBounds({ ...at, width: 420, height: modeManager.getMode() === 'OVERLAY_EXPANDED' ? 520 : 160, displayId: displayIdFor(at) },
        screen.getAllDisplays(), screen.getPrimaryDisplay(), store.DEFAULT_STATE.overlay)
      win.setBounds({ x, y, width, height })
      win.webContents.send('mode:changed', modeManager.getMode())
    }
    else {
      if (win.isMinimized()) win.restore()
      if (stopping || win.isDestroyed()) return
      setFullscreen(win, modeManager.getMode() === 'COMMAND_CENTER_FULLSCREEN')
      if (stopping) return
      notifyMode()
    }
    mark('shown', { entry: host })
  }
  function createWindow(host) {
    const overlay = host === 'overlay'
    const saved = persistedState[overlay ? 'overlay' : 'commandCenter']
    const { reason, displayId: _display, ...bounds } = store.resolveBounds(saved, screen.getAllDisplays(), screen.getPrimaryDisplay(), overlay ? store.DEFAULT_STATE.overlay : store.DEFAULT_STATE.commandCenter)
    if (reason !== 'restored') mark('state-recovered', { reason, entry: host })
    // Fixed acceptance placement keeps the captured overlay inside disposable
    // recipient clients rather than over a taskbar/titlebar or user documents.
    if (probe && overlay) {
      const area = screen.getPrimaryDisplay().workArea
      bounds.x = area.x + 120
      bounds.y = area.y + 120
    }
    const win = new BrowserWindow({ ...bounds, title: `Jarvis ${host}`, show: false,
      ...(overlay ? { frame: false, transparent: true, hasShadow: false, alwaysOnTop: true, skipTaskbar: true, resizable: false } : {}),
      webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false, sandbox: true, devTools: process.env.JARVIS_DESKTOP_DEVTOOLS === '1' },
    })
    windows.set(host, win)
    mark('created', { entry: host })
    if (overlay) {
      setInteractive(win, false)
      for (const event of ['blur', 'hide']) win.on(event, () => setInteractive(win, false))
      win.webContents.on('did-start-loading', () => setInteractive(win, false))
    }
    else {
      win.on('enter-full-screen', () => reconcileFullscreen(win, true))
      win.on('leave-full-screen', () => reconcileFullscreen(win, false))
      for (const event of ['maximize', 'unmaximize']) win.on(event, () => persist(host, win))
      win.webContents.on('before-input-event', (event, input) => {
        if (!stopping && win.isVisible() && requested.has(host) && allowedNavigation(win.webContents.mainFrame.url, host) && input?.type === 'keyDown' && input.key === 'Escape' && (win.isFullScreen() || modeManager.getMode() === 'COMMAND_CENTER_FULLSCREEN')) {
          event.preventDefault()
          requestMode('COMMAND_CENTER_WINDOWED')
        }
      })
      if (saved.maximized) win.maximize()
    }
    const wc = win.webContents
    wc.setWindowOpenHandler(() => ({ action: 'deny' }))
    for (const eventName of ['will-navigate', 'will-redirect']) wc.on(eventName, (event, url) => {
      if (!allowedNavigation(url, host)) event.preventDefault()
    })
    wc.on('will-frame-navigate', (event) => {
      if (!event.isMainFrame || !allowedNavigation(event.url, host)) event.preventDefault()
    })
    wc.on('will-attach-webview', event => event.preventDefault())
    wc.on('did-fail-load', (_event, code, _description, _url, isMainFrame) => {
      if (isMainFrame) fail('main-frame-load', { entry: host, errorCode: code })
    })
    wc.on('render-process-gone', (_event, details) => fail('renderer-gone', { entry: host, reasonCode: details.reason }))
    const deadline = later(() => fail('renderer-timeout', { entry: host }), options.readyTimeout || 15000)
    wc.once('did-finish-load', () => {
      if (stopping) return
      mark('main-frame-ready', { entry: host })
      async function observeMount() {
        if (stopping || win.isDestroyed()) return
        try {
          const ready = await wc.executeJavaScript(MOUNT_CHECK)
          if (stopping || win.isDestroyed()) return
          if (!ready) { later(observeMount, 50); return }
          cancel(deadline)
          mounted.add(host)
          mark('renderer-mounted', { entry: host })
          if (host === 'command-center' && openSettingsPending) { openSettingsPending = false; sendOpenSettings() }
          show(host, win)
          // Hold long enough for native process/HWND inventory; not visual proof.
          if (smoke) later(() => { mark('closing-window', { entry: host }); win.close(); quit() }, 1500)
          if (centerProbe && host === 'command-center' && !centerController) {
            centerController = true
            commandProbe.create({ state: centerProbe, later, quit: code => code ? fail('command-center-probe-controller') : quit(), getWindows: () => windows, getMode: () => modeManager.getMode(), requestMode, displays: () => screen.getAllDisplays().map(d => ({ id:d.id, scaleFactor:d.scaleFactor, bounds:d.bounds, workArea:d.workArea })) })
          }
          if (visualProbe && !visualController) {
            visualController = true
            nativeVisualProbe.create({ state:visualProbe, baseline:nativeVisualProbe.parseBaseline(process.argv), later, quit:code => code ? fail('visual-probe-controller') : quit(), getWindows:() => windows, requestMode, nativeImage, mark,
              displays:() => screen.getAllDisplays().map(d => ({ id:d.id, scaleFactor:d.scaleFactor, bounds:d.bounds, workArea:d.workArea })) })
          }
          if (windowProbe && !windowController) {
            windowController = true
            windowStateProbe.create({ state: windowProbe.state, later, quit: code => code ? fail('window-state-probe-controller') : quit(), getWindows: () => windows, getMode: () => modeManager.getMode(), requestMode, displays: () => screen.getAllDisplays().map(d => ({ id:d.id, scaleFactor:d.scaleFactor, bounds:d.bounds, workArea:d.workArea })) })
          }
          if (probe) createProbe({ state: probe, later, quit: code => code ? fail('overlay-probe-controller') : quit(), getWindow: () => windows.get('overlay'), getMode: () => modeManager.getMode(), requestMode })
        } catch { fail('mount-observation', { entry: host }) }
      }
      void observeMount()
    })
    win.on('move', () => persist(host, win))
    win.on('resize', () => persist(host, win))
    win.on('close', event => {
      if (stopping || smoke) return
      event.preventDefault()
      if (overlay) { setInteractive(win, false); win.hide() }
      else requestMode('OVERLAY_COMPACT')
    })
    win.on('closed', () => { cancel(fullscreenPending.get(win)?.timer); fullscreenPending.delete(win); mounted.delete(host); windows.delete(host); mark('window-closed', { entry: host }) })
    Promise.resolve().then(() => { if (!stopping) return win.loadURL(`app://${host}/`) }).catch(() => fail('load-url-rejected', { entry: host }))
    return win
  }
  function requestMode(target) {
    if (stopping || !modeManager) return false
    if (target === modeManager.getMode()) {
      const host = target.startsWith('COMMAND_CENTER') ? 'command-center' : target === 'HIDDEN' ? null : 'overlay'
      if (host && windows.has(host)) { requested.add(host); show(host, windows.get(host)) }
      return !stopping
    }
    if (!modeManager.canTransition(target)) return false
    const settle = modeManager.beginTransition(target)
    requested.clear()
    const host = target.startsWith('COMMAND_CENTER') ? 'command-center' : target === 'HIDDEN' ? null : 'overlay'
    try {
      if (host) requested.add(host)
      for (const [entry, win] of windows) if (entry !== host) {
        if (entry === 'overlay') setInteractive(win, false)
        else setFullscreen(win, false)
        win.hide()
      }
      if (stopping) { settle(); return false }
      if (host && (!windows.has(host) || windows.get(host).isDestroyed())) createWindow(host)
      settle()
      if (host) show(host, windows.get(host))
      if (stopping) return false
      if (!smoke && !probe && !centerProbe && !visualProbe) { persistedState.lastActiveWorkspace = target; store.saveState(stateFile, persistedState) }
      return true
    } catch { settle(); fail('window-or-state'); return false }
  }
  function onDisplayChange(event) {
    if (stopping || !modeManager) return
    try {
      const displays = screen.getAllDisplays(), primary = screen.getPrimaryDisplay(), repositioned = []
      for (const [host, win] of windows) {
        if (win.isDestroyed() || !win.isVisible()) continue
        const key = host === 'overlay' ? 'overlay' : 'commandCenter'
        const known = persistedState[key].displayId
        if (host === 'command-center' && (win.isFullScreen() || win.isMaximized())) {
          // Only a vanished display forces the center back to ordinary bounds.
          if (known == null || displays.some(d => d.id === known)) continue
          if (modeManager.getMode() === 'COMMAND_CENTER_FULLSCREEN') requestMode('COMMAND_CENTER_WINDOWED')
          else if (win.isFullScreen()) setFullscreen(win, false)
          if (win.isMaximized()) win.unmaximize()
          if (win.isFullScreen() || win.isMaximized() || win.isDestroyed()) continue
        }
        const now = win.getBounds()
        const next = store.resolveBounds({ ...now, displayId: known }, displays, primary, store.DEFAULT_STATE[key])
        if (next.x === now.x && next.y === now.y && next.width === now.width && next.height === now.height) continue
        win.setBounds({ x: next.x, y: next.y, width: next.width, height: next.height })
        persist(host, win)
        repositioned.push(host)
      }
      mark('display-change', { event, repositioned })
    } catch { fail('display-change') }
  }
  const isNormalRun = () => !smoke && !probe && !centerProbe && !windowProbe && !trayProbe && !visualProbe
  const toggleOverlay = () => requestMode(modeManager.getMode().startsWith('OVERLAY_') ? 'HIDDEN' : 'OVERLAY_COMPACT')
  const toggleCommandCenter = () => requestMode(modeManager.getMode().startsWith('COMMAND_CENTER') ? 'OVERLAY_COMPACT' : 'COMMAND_CENTER_WINDOWED')
  function sendOpenSettings() {
    const win = windows.get('command-center')
    if (win && !win.isDestroyed() && mounted.has('command-center')) win.webContents.send('desktop:open-settings')
    else openSettingsPending = true // flushed once the renderer mounts
  }
  function openSettings() {
    requestMode('COMMAND_CENTER_WINDOWED')
    sendOpenSettings()
  }
  const loginItemQuery = () => ({ path: process.execPath, args: [...(process.defaultApp ? [app.getAppPath()] : []), '--launched-at-login'], ...(trayProbe ? { name: app.getName() } : {}) })
  // Electron compares registered args literally, so an args-quoting difference hides a real Run entry;
  // executableWillLaunchAtLogin ignores args and keeps the reading truthful.
  const loginActual = () => { const s = app.getLoginItemSettings(loginItemQuery()); return s.openAtLogin === true || s.executableWillLaunchAtLogin === true }
  function applyLoginItem(reason) {
    if ((trayProbe && !trayProbe.loginItem) || typeof app.getLoginItemSettings !== 'function' || typeof app.setLoginItemSettings !== 'function') return
    const openAtLogin = persistedState.launchAtLogin === true
    try {
      const query = loginItemQuery()
      if (loginActual() === openAtLogin) return
      app.setLoginItemSettings({ openAtLogin, ...query })
      mark('login-item-applied', { openAtLogin, reason })
    } catch { mark('login-item-failed', { openAtLogin, reason }) }
    // Also reconcile startup failures, not only tray checkbox changes.
    try {
      const actual = loginActual()
      if (typeof actual === 'boolean' && actual !== persistedState.launchAtLogin) {
        persistedState.launchAtLogin = actual
        store.saveState(stateFile, persistedState)
        mark('login-item-failed', { openAtLogin, reason: 'os-mismatch' })
      }
    } catch { mark('login-item-failed', { openAtLogin, reason: 'reconcile' }) }
  }
  // Persists first, applies to the OS, then re-reads the OS so the stored value stays truthful.
  function setLaunchAtLogin(value) {
    if (stopping || (trayProbe && !trayProbe.loginItem) || typeof value !== 'boolean') return persistedState?.launchAtLogin === true
    const previous = persistedState.launchAtLogin
    persistedState.launchAtLogin = value
    try { store.saveState(stateFile, persistedState) } catch {
      persistedState.launchAtLogin = previous
      mark('login-item-failed', { openAtLogin: value, reason: 'state-write' })
      return previous === true
    }
    applyLoginItem('user')
    rebuildTray()
    broadcast('desktop:settings', desktopSettings())
    return persistedState.launchAtLogin === true
  }
  async function stopCoreWithConfirm() {
    if (stopping || trayProbe || coreStopPending || !coreBridge || !dialog || coreBridge.getState() !== 'connected') return
    coreStopPending = true
    try {
      let response = 0
      try {
        ({ response } = await dialog.showMessageBox({ type: 'warning', buttons: ['Cancel', 'Stop Jarvis Core'], defaultId: 0, cancelId: 0, noLink: true,
          title: 'Stop Jarvis Core', message: 'Stop Jarvis Core?', detail: 'This ends voice activation and all Jarvis services, not only this window. Use "Exit desktop" to close just the desktop app.' }))
      } catch { response = 0 }
      if (stopping) return
      if (response !== 1) { mark('core-stop-declined'); return }
      if (coreBridge.getState() !== 'connected') { mark('core-stop-failed', { reason: 'not-connected' }); return }
      mark('core-stop-requested')
      try { await coreBridge.stopCore(); mark('core-stop-accepted') } catch (error) { mark('core-stop-failed', { reason: typeof error?.code === 'string' ? error.code.slice(0, 32) : 'failed' }) }
    } finally { coreStopPending = false }
  }
  const trayActions = {
    openCommandCenter: () => requestMode('COMMAND_CENTER_WINDOWED'),
    toggleOverlay,
    openSettings,
    setLaunchAtLogin,
    exitDesktop: () => quit(0),
    stopCore: () => { void stopCoreWithConfirm() },
  }
  function rebuildTray() {
    if (!tray || stopping) return
    try {
      tray.setContextMenu(Menu.buildFromTemplate(buildTrayMenu({ mode: modeManager.getMode(), launchAtLogin: persistedState.launchAtLogin === true,
        shortcuts: persistedState.shortcuts, shortcutStatus, coreState: trayProbe ? 'disconnected' : coreBridge?.getState() }, trayActions)))
    } catch { mark('tray-failed', { reason: 'menu' }) }
  }
  function createTray() {
    if (!Tray || !Menu || !nativeImage) return
    try {
      tray = new Tray(trayIcon(nativeImage))
      tray.setToolTip('Jarvis')
      tray.on('click', toggleOverlay)
      nativeUp = true
      rebuildTray()
      modeManager.onChange(rebuildTray)
      mark('tray-ready')
    } catch {
      try { tray?.destroy() } catch { /* release a partially constructed tray */ }
      tray = null
      mark('tray-failed', { reason: 'create' })
    }
  }
  function registerShortcuts(rejections = []) {
    for (const rejection of rejections) mark('shortcut-rejected', { action: rejection.action, accelerator: rejection.value, reason: rejection.reason })
    if (!globalShortcut) return
    nativeUp = true
    const handlers = { toggleOverlay, toggleCommandCenter }
    for (const action of SHORTCUT_ACTIONS) {
      const accelerator = persistedState.shortcuts?.[action]
      let reason = null
      try {
        if (!globalShortcut.register(accelerator, () => { try { if (!stopping) handlers[action]() } catch { /* OS callback must not throw */ } })) reason = 'registration-failed'
      } catch { reason = 'register-threw' }
      shortcutStatus[action] = reason ? 'unavailable' : 'registered'
      mark(reason ? 'shortcut-unavailable' : 'shortcut-registered', { action, accelerator, ...(reason ? { reason } : {}) })
    }
  }
  function owned(event, onlyOverlay = false) {
    if (stopping || !event) return false
    for (const [host, win] of windows) {
      if ((!onlyOverlay || host === 'overlay') && !win.isDestroyed() && event.sender === win.webContents && event.senderFrame === win.webContents.mainFrame && allowedNavigation(event.senderFrame?.url, host)) return true
    }
    return false
  }
  function desktopSettings() {
    return { launchAtLogin: persistedState.launchAtLogin === true,
      shortcuts: SHORTCUT_ACTIONS.map(action => ({ action, accelerator: persistedState.shortcuts[action], registered: shortcutStatus[action] === 'registered' })) }
  }
  function registerIpc() {
    ipcMain.handle('desktop:get-settings', event => owned(event) ? desktopSettings() : null)
    ipcMain.handle('desktop:set-launch-at-login', (event, value) => {
      const center = windows.get('command-center')
      if (!owned(event) || !center || event.sender !== center.webContents || !isNormalRun() || typeof value !== 'boolean') return null
      setLaunchAtLogin(value)
      return desktopSettings()
    })
    ipcMain.handle('mode:get', event => owned(event) ? modeManager.getMode() : null)
    ipcMain.on('mode:request', (event, target) => {
      if (!smoke && owned(event) && [...windows.values()].some(win => !win.isDestroyed() && win.webContents === event.sender && win.isVisible()) && typeof target === 'string' && target.length <= 32 && MODES.includes(target)) {
        // Overlay may resize itself or open the center; only the center owns fullscreen.
        if (owned(event, true) && target === 'COMMAND_CENTER_FULLSCREEN') return
        if ((probe || windowProbe) && !['OVERLAY_COMPACT', 'OVERLAY_EXPANDED'].includes(target)) return
        requestMode(target)
      }
    })
    ipcMain.on('overlay:set-interactive', (event, interactive) => {
      if (!stopping && owned(event, true) && typeof interactive === 'boolean') {
        const win = windows.get('overlay')
        if (!interactive || (win.isVisible() && modeManager.getMode().startsWith('OVERLAY_'))) setInteractive(win, interactive)
      }
    })
    ipcMain.on('overlay:move-to', (event, payload) => {
      if (!owned(event, true) || !payload || typeof payload !== 'object' || Array.isArray(payload)) return
      const { x, y } = payload
      if (typeof x !== 'number' || typeof y !== 'number' || !Number.isFinite(x) || !Number.isFinite(y) || Math.abs(x) > 100000 || Math.abs(y) > 100000) return
      windows.get('overlay').setPosition(Math.round(x), Math.round(y))
    })
    ipcMain.handle('core:get-state', event => owned(event) && coreBridge ? coreBridge.getState() : null)
    ipcMain.handle('core:get-replay', event => owned(event) && coreBridge ? coreBridge.getReplay() : [])
    ipcMain.handle('core:control', async (event, action) => {
      if (!owned(event) || !coreBridge) return { ok: false, error: 'denied' }
      if (typeof action !== 'string' || !CORE_ACTIONS.includes(action)) return { ok: false, error: 'unsupported action' }
      try {
        await coreBridge.control(action)
        return { ok: true }
      } catch (error) {
        return { ok: false, error: typeof error?.code === 'string' ? error.code.slice(0, 32) : 'failed' }
      }
    })
  }
  function startCoreBridge() {
    coreBridge = options.coreBridge || createCoreBridge({
      controlPort: parseControlPort(process.env.JARVIS_CONTROL_PORT),
      log: record => console.log(`[jarvis-core] ${JSON.stringify(record)}`),
    })
    coreBridge.onState(state => { broadcast('core:state', state); rebuildTray() })
    coreBridge.onEvent(event => broadcast('core:event', event))
    coreBridge.start()
  }
  // Baseline for every webContents, including any not created by createWindow.
  app.on('web-contents-created', (_event, contents) => {
    contents.setWindowOpenHandler(() => { mark('window-open-denied'); return { action: 'deny' } })
    contents.on('will-attach-webview', event => { mark('webview-denied'); event.preventDefault() })
    for (const eventName of ['will-navigate', 'will-redirect']) contents.on(eventName, (event, url) => {
      if (!allowedNavigation(url, 'overlay') && !allowedNavigation(url, 'command-center')) { mark('navigation-denied'); event.preventDefault() }
    })
  })
  function denyPermissions() {
    const ses = electron.session?.defaultSession
    if (!ses) return
    ses.setPermissionRequestHandler((_contents, permission, callback) => { mark('permission-denied', { permission: String(permission).slice(0, 32) }); callback(false) })
    ses.setPermissionCheckHandler(() => false)
  }
  app.on('before-quit', () => {
    if (!stopping) { stopping = true; clearTimers(); unlistenDisplays(); stopBridge(); releaseNative(); mark('closing', { code: 0 }); quitTimer = later(() => app.exit(1), 2000) }
  })
  app.on('quit', (_event, code) => { cancel(quitTimer); clearTimers(); mark('quit', { code: code || (failed ? 1 : 0) }) })
  app.on('window-all-closed', () => { /* ordinary close remains mode/hide, explicit quit owns exit */ })
  try {
    visualProbe = options.visualProbe === undefined ? nativeVisualProbe.parse(process.argv, parseSmoke, ROOT) : options.visualProbe
    trayProbe = options.trayProbe === undefined ? nativeTrayProbe.parse(process.argv, parseSmoke, ROOT) : options.trayProbe
    windowProbe = options.windowProbe === undefined ? windowStateProbe.parse(process.argv, parseSmoke, ROOT) : options.windowProbe
    centerProbe = options.centerProbe === undefined ? commandProbe.parse(process.argv, parseSmoke, ROOT) : options.centerProbe
    probe = options.probe === undefined ? parseProbe(process.argv, parseSmoke, ROOT) : options.probe
    smoke = options.smoke === undefined ? parseSmoke(process.argv) : options.smoke
    const isolated = visualProbe || trayProbe?.state || windowProbe?.state || centerProbe || probe || smoke?.state
    if (isolated) {
      app.setPath('userData', isolated)
      app.setPath('sessionData', isolated)
      app.setPath('crashDumps', isolated)
    }
    stateFile = path.join(app.getPath('userData'), 'window-state.json')
    protocol.registerSchemesAsPrivileged([{ scheme: 'app', privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true } }])
    app.disableHardwareAcceleration()
    for (const flag of ['disable-gpu', 'disable-gpu-compositing', 'in-process-gpu']) app.commandLine.appendSwitch(flag)
    // Acceptance probe maps CSS px to physical pixels 1:1; the host may run at 150% display scaling.
    if (probe || centerProbe || windowProbe || visualProbe) app.commandLine.appendSwitch('force-device-scale-factor', '1')
    if (visualProbe) app.commandLine.appendSwitch('force-prefers-reduced-motion')
    if (!app.requestSingleInstanceLock()) { mark('duplicate-instance'); quit(); return { requestMode, quit } }
    app.on('second-instance', () => {
      if (stopping || smoke) return
      const visible = [...windows.values()].find(win => !win.isDestroyed() && win.isVisible())
      if (visible) visible.focus(); else requestMode('OVERLAY_COMPACT')
    })
    // Includes whenReady in the bounded startup budget.
    const startupDeadline = later(() => fail('app-ready-timeout'), 15000)
    app.whenReady().then(() => {
      if (stopping) return
      cancel(startupDeadline)
      try { ui.preflight(); mark('dist-ready') } catch { fail('dist-missing-or-incomplete', { action: 'Build ui/dist with npm --prefix ui run build' }); return }
      const loaded = store.loadStateWithDiagnostics(stateFile)
      persistedState = loaded.state
      if (loaded.recovered) mark('state-recovered', { reason: loaded.recovered })
      modeManager = createModeManager('HIDDEN')
      protocol.handle('app', request => {
        try {
          const response = ui.respond(request)
          if (response.status >= 400) fail('ui-protocol-response', { status: response.status })
          return response
        } catch {
          fail('ui-protocol-error')
          return new Response('unreadable asset', { status: 500 })
        }
      })
      denyPermissions()
      registerIpc()
      if (typeof screen.on === 'function') for (const event of ['display-removed', 'display-added', 'display-metrics-changed']) {
        const listener = () => onDisplayChange(event)
        displayListeners.push([event, listener])
        screen.on(event, listener)
      }
      if (trayProbe) modeManager.onChange(mode => mark('tray-probe-mode', { mode }))
      const target = visualProbe ? 'COMMAND_CENTER_WINDOWED' : trayProbe ? 'HIDDEN' : windowProbe ? 'OVERLAY_COMPACT' : centerProbe ? 'COMMAND_CENTER_WINDOWED' : probe ? 'OVERLAY_COMPACT' : smoke ? (smoke.entry === 'overlay' ? 'OVERLAY_COMPACT' : 'COMMAND_CENTER_WINDOWED') : (process.argv.includes('--launched-at-login') ? 'HIDDEN' : persistedState.initialMode || 'OVERLAY_COMPACT')
      if (target === 'OVERLAY_EXPANDED') requestMode('OVERLAY_COMPACT')
      if (target === 'COMMAND_CENTER_FULLSCREEN') requestMode('COMMAND_CENTER_WINDOWED')
      if (!requestMode(target)) { fail('invalid-initial-transition'); return }
      if (!visualProbe) startCoreBridge() // Captures never attach to or control live Core.
      if (isNormalRun() || trayProbe) {
        // Probe never reconciles the real login item on startup, even when opted in.
        if (isNormalRun()) applyLoginItem('startup')
        createTray()
        registerShortcuts(loaded.shortcutRejections)
        rebuildTray()
      }
      if (trayProbe) {
        mark('tray-probe-ready', { mode: modeManager.getMode(), loginItem: trayProbe.loginItem, loginName: app.getName() })
        nativeTrayProbe.create({ ...trayProbe, later, quit, setLaunchAtLogin, mark })
      }
    }).catch(() => fail('bootstrap-ready'))
  } catch { fail('bootstrap-configuration') }
  return { requestMode, quit }
}

// Electron 44 imports its application entry rather than making it require.main.
// Node contract-test imports must still remain side-effect free.
if (require.main === module || (process.versions.electron && process.type === 'browser')) startDesktop(require('electron'))
module.exports = { startDesktop, parseSmoke, parseControlPort, MOUNT_CHECK }
