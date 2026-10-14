'use strict'

const path = require('node:path')

const TRAY_ICON = path.join(__dirname, '..', 'assets', 'tray.png')
const ACTION_LABELS = { toggleOverlay: 'Toggle overlay', toggleCommandCenter: 'Toggle Command Center' }

// Pure: returns an Electron menu template; every side effect goes through `actions`.
function buildTrayMenu({ mode, launchAtLogin, shortcuts = {}, shortcutStatus = {}, coreState }, actions) {
  const overlayVisible = typeof mode === 'string' && mode.startsWith('OVERLAY_')
  const shortcutItems = Object.keys(ACTION_LABELS).filter(action => shortcuts[action]).map(action => ({
    label: `${ACTION_LABELS[action]}: ${shortcuts[action]}${shortcutStatus[action] === 'registered' ? '' : ' (unavailable)'}`,
    enabled: false,
  }))
  return [
    { label: 'Open Command Center', click: () => actions.openCommandCenter() },
    { label: overlayVisible ? 'Hide overlay' : 'Show overlay', click: () => actions.toggleOverlay() },
    { label: 'Settings', click: () => actions.openSettings() },
    { label: 'Launch at sign-in', type: 'checkbox', checked: Boolean(launchAtLogin), click: item => actions.setLaunchAtLogin(Boolean(item?.checked)) },
    { type: 'separator' },
    ...shortcutItems,
    ...(shortcutItems.length ? [{ type: 'separator' }] : []),
    { label: 'Exit desktop (Jarvis keeps running)', click: () => actions.exitDesktop() },
    { label: 'Stop Jarvis Core…', enabled: coreState === 'connected', click: () => actions.stopCore() },
  ]
}

function trayIcon(nativeImage) {
  const icon = nativeImage.createFromPath(TRAY_ICON)
  const small = icon.resize({ width: 16, height: 16 })
  const large = icon.resize({ width: 32, height: 32 })
  small.addRepresentation({ scaleFactor: 2, buffer: large.toPNG() })
  return small
}

module.exports = { buildTrayMenu, trayIcon, TRAY_ICON, ACTION_LABELS }
