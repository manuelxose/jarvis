'use strict'

const { contextBridge, ipcRenderer } = require('electron')

// The entire native surface exposed to the renderer. No `require`, no
// arbitrary IPC channel, no shell/filesystem access — section 4 and 14's
// "frontend must never obtain unrestricted access to the operating system"
// enforced here, not by convention.
const modes = ['HIDDEN', 'OVERLAY_COMPACT', 'OVERLAY_EXPANDED', 'COMMAND_CENTER_WINDOWED', 'COMMAND_CENTER_FULLSCREEN']
const coreStates = ['connecting', 'connected', 'reconnecting', 'disconnected', 'auth-failure', 'incompatible-version']
const isPlainEvent = (value) => Boolean(value) && typeof value === 'object' && !Array.isArray(value) && value.v === 1 && typeof value.name === 'string'
const settingsActions = ['toggleOverlay', 'toggleCommandCenter']
const isSettings = value => Boolean(value) && typeof value === 'object' && !Array.isArray(value) &&
  typeof value.launchAtLogin === 'boolean' && Array.isArray(value.shortcuts) && value.shortcuts.length === 2 &&
  settingsActions.every(action => value.shortcuts.filter(item => item && item.action === action &&
    typeof item.accelerator === 'string' && item.accelerator.length > 0 && item.accelerator.length <= 64 && typeof item.registered === 'boolean').length === 1)
function subscribe(channel, accept, callback) {
  if (typeof callback !== 'function') return () => {}
  let active = true
  const listener = (_event, value) => { if (active && accept(value)) callback(value) }
  ipcRenderer.on(channel, listener)
  return () => {
    if (!active) return
    active = false
    ipcRenderer.removeListener(channel, listener)
  }
}
contextBridge.exposeInMainWorld('jarvisDesktop', {
  getMode: async () => {
    const mode = await ipcRenderer.invoke('mode:get')
    return modes.includes(mode) ? mode : null
  },
  requestMode: (mode) => { if (modes.includes(mode)) ipcRenderer.send('mode:request', mode) },
  onModeChanged: (callback) => {
    if (typeof callback !== 'function') return () => {}
    let active = true
    const listener = (_event, mode) => { if (active && modes.includes(mode)) callback(mode) }
    ipcRenderer.on('mode:changed', listener)
    return () => {
      if (!active) return
      active = false
      ipcRenderer.removeListener('mode:changed', listener)
    }
  },
  settings: {
    get: async () => {
      const value = await ipcRenderer.invoke('desktop:get-settings')
      return isSettings(value) ? value : null
    },
    setLaunchAtLogin: async value => {
      if (typeof value !== 'boolean') return null
      const result = await ipcRenderer.invoke('desktop:set-launch-at-login', value)
      return isSettings(result) ? result : null
    },
    onChanged: callback => subscribe('desktop:settings', isSettings, callback),
    onOpenRequested: callback => subscribe('desktop:open-settings', value => value === undefined, callback),
  },
  core: {
    getState: async () => {
      const state = await ipcRenderer.invoke('core:get-state')
      return coreStates.includes(state) ? state : null
    },
    getReplay: async () => {
      const replay = await ipcRenderer.invoke('core:get-replay')
      return Array.isArray(replay) ? replay.filter(isPlainEvent) : []
    },
    onState: (callback) => subscribe('core:state', value => coreStates.includes(value), callback),
    onEvent: (callback) => subscribe('core:event', value => isPlainEvent(value), callback),
    control: async (action) => {
      if (action !== 'activate' && action !== 'sleep') return { ok: false, error: 'unsupported action' }
      const result = await ipcRenderer.invoke('core:control', action)
      return result && result.ok === true ? { ok: true } : { ok: false, error: typeof result?.error === 'string' ? result.error.slice(0, 64) : 'failed' }
    },
  },
  setOverlayInteractive: (interactive) => { if (typeof interactive === 'boolean') ipcRenderer.send('overlay:set-interactive', interactive) },
  moveOverlayTo: (x, y) => {
    if (typeof x === 'number' && typeof y === 'number' && Number.isFinite(x) && Number.isFinite(y) && Math.abs(x) <= 100000 && Math.abs(y) <= 100000) ipcRenderer.send('overlay:move-to', { x, y })
  },
})
