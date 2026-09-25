'use strict'

const path = require('node:path')
const fs = require('node:fs')
const { app, BrowserWindow, ipcMain, screen, protocol } = require('electron')
const { createModeManager } = require('./mode-manager')
const { loadState, saveState, resolveBounds } = require('./window-state-store')

// ui/dist has two entry points (index.html = Command Center, overlay.html =
// Overlay) with root-absolute asset paths (Vite default `base: '/'`), which
// break under `file://`. Serving it behind a custom `app://` scheme instead
// of `loadFile` keeps those paths working, gives an explicit CSP surface,
// and matches section 14's "no unsafe remote content loading" — no window
// in this app ever navigates to http(s)://.
const UI_DIST = path.resolve(__dirname, '..', '..', 'ui', 'dist')
const APP_SCHEME = 'app'

protocol.registerSchemesAsPrivileged([
  { scheme: APP_SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: false } },
])

const STATE_FILE = path.join(app.getPath('userData'), 'window-state.json')

// ponytail: GPU process init fails hard in some Windows session contexts
// (non-interactive window station, remote/automation-launched processes,
// GPU driver/policy restrictions) with a fatal "GPU process isn't usable" —
// Chromium then exits instead of falling back. The overlay/Command Center
// only need 2D compositing, so software rendering is an acceptable ceiling,
// not a real capability loss. Upgrade path: drop this once native
// hardware-accelerated launch is confirmed stable on the target machines.
// Must be set before whenReady(); CLI flags of the same name passed after
// the app path are treated as app argv, not Chromium switches, so this is
// the only reliable place to set them.
app.disableHardwareAcceleration()
app.commandLine.appendSwitch('disable-gpu')
app.commandLine.appendSwitch('disable-gpu-compositing')
app.commandLine.appendSwitch('in-process-gpu')

let modeManager
let overlayWindow = null
let commandCenterWindow = null
let isQuitting = false
let persistedState = null

function serveAppProtocol() {
  protocol.handle(APP_SCHEME, (request) => {
    const url = new URL(request.url)
    // app://overlay/  -> ui/dist/overlay.html ; app://command-center/ -> ui/dist/index.html
    const routeFile = url.hostname === 'overlay' ? 'overlay.html' : 'index.html'
    let relPath = url.pathname === '/' || url.pathname === '' ? routeFile : url.pathname.replace(/^\//, '')
    const filePath = path.normalize(path.join(UI_DIST, relPath))
    if (!filePath.startsWith(UI_DIST)) {
      return new Response('forbidden', { status: 403 })
    }
    try {
      const data = fs.readFileSync(filePath)
      return new Response(data, { headers: { 'Content-Type': contentType(filePath) } })
    } catch {
      return new Response('not found', { status: 404 })
    }
  })
}

function contentType(filePath) {
  const ext = path.extname(filePath)
  return (
    {
      '.html': 'text/html',
      '.js': 'text/javascript',
      '.css': 'text/css',
      '.svg': 'image/svg+xml',
      '.json': 'application/json',
    }[ext] || 'application/octet-stream'
  )
}

function applyCsp(win) {
  win.webContents.session.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [
          "default-src 'self' app:; script-src 'self' app:; style-src 'self' app: 'unsafe-inline'; img-src 'self' app: data:; connect-src 'self' app: ws://127.0.0.1:* http://127.0.0.1:*; object-src 'none'; base-uri 'none'",
        ],
      },
    })
  })
}

// Blocks the security holes section 14 explicitly calls out: no arbitrary
// window.open, no navigation away from the app:// content we serve.
function hardenWindow(win) {
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  win.webContents.on('will-navigate', (event, url) => {
    if (!url.startsWith(`${APP_SCHEME}://`)) event.preventDefault()
  })
}

function createOverlayWindow() {
  const saved = persistedState.overlay
  const displays = screen.getAllDisplays()
  const primary = screen.getPrimaryDisplay()
  const bounds = resolveBounds(saved, displays, primary)

  const win = new BrowserWindow({
    ...bounds,
    frame: false,
    transparent: true,
    hasShadow: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  })

  // Click-through: the renderer forwards mousemove and tells us, per pixel,
  // whether it landed on real content vs. the transparent background (see
  // ui/src/shell/overlay-interop.ts). Start pass-through so the overlay
  // never blocks the desktop underneath before the renderer is ready.
  win.setIgnoreMouseEvents(true, { forward: true })

  applyCsp(win)
  hardenWindow(win)

  win.on('move', () => persistOverlayBounds(win))
  win.on('resize', () => persistOverlayBounds(win))
  win.on('close', (event) => {
    if (isQuitting) return
    event.preventDefault()
    win.hide()
  })

  win.loadURL('app://overlay/')
  return win
}

function createCommandCenterWindow() {
  const saved = persistedState.commandCenter
  const displays = screen.getAllDisplays()
  const primary = screen.getPrimaryDisplay()
  const bounds = resolveBounds(saved, displays, primary)

  const win = new BrowserWindow({
    ...bounds,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  })

  if (saved.maximized) win.maximize()

  applyCsp(win)
  hardenWindow(win)

  win.on('move', () => persistCommandCenterBounds(win))
  win.on('resize', () => persistCommandCenterBounds(win))
  win.on('close', (event) => {
    if (isQuitting) return
    event.preventDefault()
    requestMode('OVERLAY_COMPACT')
  })

  win.loadURL('app://command-center/')
  return win
}

function persistOverlayBounds(win) {
  if (win.isDestroyed()) return
  const b = win.getBounds()
  persistedState.overlay = { ...persistedState.overlay, ...b }
  saveState(STATE_FILE, persistedState)
}

function persistCommandCenterBounds(win) {
  if (win.isDestroyed()) return
  const b = win.getBounds()
  persistedState.commandCenter = { ...persistedState.commandCenter, ...b, maximized: win.isMaximized() }
  saveState(STATE_FILE, persistedState)
}

function applyMode(mode) {
  switch (mode) {
    case 'HIDDEN':
      overlayWindow?.hide()
      commandCenterWindow?.hide()
      break
    case 'OVERLAY_COMPACT':
    case 'OVERLAY_EXPANDED':
      commandCenterWindow?.hide()
      if (!overlayWindow || overlayWindow.isDestroyed()) overlayWindow = createOverlayWindow()
      overlayWindow.show()
      overlayWindow.webContents.send('mode:changed', mode)
      break
    case 'COMMAND_CENTER_WINDOWED':
      overlayWindow?.hide()
      if (!commandCenterWindow || commandCenterWindow.isDestroyed()) commandCenterWindow = createCommandCenterWindow()
      if (commandCenterWindow.isFullScreen()) commandCenterWindow.setFullScreen(false)
      commandCenterWindow.show()
      break
    case 'COMMAND_CENTER_FULLSCREEN':
      overlayWindow?.hide()
      if (!commandCenterWindow || commandCenterWindow.isDestroyed()) commandCenterWindow = createCommandCenterWindow()
      commandCenterWindow.show()
      commandCenterWindow.setFullScreen(true)
      break
    default:
      throw new Error(`applyMode: unhandled mode ${mode}`)
  }
}

function requestMode(target) {
  if (!modeManager.canTransition(target)) {
    console.warn(`mode-manager: rejected ${modeManager.getMode()} -> ${target} (not allowed or transition in flight)`)
    return false
  }
  const from = modeManager.getMode()
  const settle = modeManager.beginTransition(target)
  try {
    applyMode(target)
  } finally {
    settle()
  }
  console.log(`[jarvis-desktop] mode: ${from} -> ${target}`)
  persistedState.lastActiveWorkspace = target
  saveState(STATE_FILE, persistedState)
  return true
}

function registerIpc() {
  ipcMain.handle('mode:get', () => modeManager.getMode())
  ipcMain.on('mode:request', (event, target) => {
    requestMode(target)
  })
  ipcMain.on('overlay:set-interactive', (event, interactive) => {
    if (!overlayWindow || overlayWindow.isDestroyed()) return
    if (interactive) overlayWindow.setIgnoreMouseEvents(false)
    else overlayWindow.setIgnoreMouseEvents(true, { forward: true })
  })
  ipcMain.on('overlay:move-to', (event, { x, y }) => {
    if (!overlayWindow || overlayWindow.isDestroyed()) return
    overlayWindow.setPosition(Math.round(x), Math.round(y))
  })
}

const gotLock = app.requestSingleInstanceLock()
if (!gotLock) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (overlayWindow && !overlayWindow.isDestroyed() && overlayWindow.isVisible()) {
      overlayWindow.focus()
    } else if (commandCenterWindow && !commandCenterWindow.isDestroyed() && commandCenterWindow.isVisible()) {
      commandCenterWindow.focus()
    } else {
      requestMode('OVERLAY_COMPACT')
    }
  })

  app.whenReady().then(() => {
    console.log(`[jarvis-desktop] ready, userData=${app.getPath('userData')}`)
    persistedState = loadState(STATE_FILE)
    modeManager = createModeManager('HIDDEN')
    serveAppProtocol()
    registerIpc()
    requestMode(persistedState.initialMode || 'OVERLAY_COMPACT')
  })

  app.on('before-quit', () => {
    isQuitting = true
  })

  app.on('window-all-closed', () => {
    // No system tray yet (P3.7) — closing every window would otherwise make
    // the app unrecoverable. Windows are hidden, not closed, by the
    // `close` handlers above, so this only fires on an explicit quit path.
  })
}

module.exports = { requestMode }
