'use strict'

const fs = require('node:fs')
const path = require('node:path')

// Persisted window/display preferences (brief section 7 + 13). Pure
// functions apart from the fs calls in load/save, so the coordinate-math
// (the part that actually breaks on a disconnected monitor) is unit-testable
// without touching disk or Electron's `screen` module.

const { MODES } = require('./mode-manager')
const { DEFAULT_SHORTCUTS, sanitizeShortcuts } = require('./shortcuts')

const DEFAULT_STATE = Object.freeze({
  initialMode: 'OVERLAY_COMPACT',
  overlay: { displayId: null, x: null, y: null, width: 360, height: 72 },
  commandCenter: { displayId: null, x: null, y: null, width: 1280, height: 800, maximized: false },
  fullscreenPreference: false,
  lastActiveWorkspace: null,
  launchAtLogin: false,
  shortcuts: { ...DEFAULT_SHORTCUTS },
})

const isInt = (v) => Number.isFinite(v) && Math.abs(v) <= 100000
const pickPos = (v) => (isInt(v) ? Math.round(v) : null)
const pickSize = (v, d) => (Number.isFinite(v) && v >= 1 && v <= 16384 ? Math.round(v) : d)

function sanitizeWindow(raw, defaults) {
  const r = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {}
  const out = {
    displayId: Number.isFinite(r.displayId) ? r.displayId : null,
    x: pickPos(r.x),
    y: pickPos(r.y),
    width: pickSize(r.width, defaults.width),
    height: pickSize(r.height, defaults.height),
  }
  if ('maximized' in defaults) out.maximized = typeof r.maximized === 'boolean' ? r.maximized : defaults.maximized
  return out
}

function sanitizeState(raw) {
  const r = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {}
  return {
    initialMode: MODES.includes(r.initialMode) ? r.initialMode : DEFAULT_STATE.initialMode,
    overlay: sanitizeWindow(r.overlay, DEFAULT_STATE.overlay),
    commandCenter: sanitizeWindow(r.commandCenter, DEFAULT_STATE.commandCenter),
    fullscreenPreference:
      typeof r.fullscreenPreference === 'boolean' ? r.fullscreenPreference : DEFAULT_STATE.fullscreenPreference,
    lastActiveWorkspace: MODES.includes(r.lastActiveWorkspace) ? r.lastActiveWorkspace : null,
    launchAtLogin: typeof r.launchAtLogin === 'boolean' ? r.launchAtLogin : DEFAULT_STATE.launchAtLogin,
    shortcuts: sanitizeShortcuts(r.shortcuts).shortcuts,
  }
}

function loadStateWithDiagnostics(filePath) {
  let raw
  try {
    raw = fs.readFileSync(filePath, 'utf8')
  } catch (err) {
    if (err.code !== 'ENOENT') {
      console.error('window-state-store: failed to read state, using defaults', err)
      return { state: sanitizeState(null), recovered: 'unreadable-file', shortcutRejections: [] }
    }
    return { state: sanitizeState(null), recovered: null, shortcutRejections: [] }
  }
  let parsed
  try {
    parsed = JSON.parse(raw)
  } catch (err) {
    console.error('window-state-store: corrupt state file, using defaults', err)
    return { state: sanitizeState(null), recovered: 'corrupt-file', shortcutRejections: [] }
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return { state: sanitizeState(null), recovered: 'corrupt-file', shortcutRejections: [] }
  }
  const badMode = 'initialMode' in parsed && !MODES.includes(parsed.initialMode)
  return {
    state: sanitizeState(parsed),
    recovered: badMode ? 'invalid-initial-mode' : null,
    shortcutRejections: sanitizeShortcuts(parsed.shortcuts).rejected,
  }
}

function loadState(filePath) {
  return loadStateWithDiagnostics(filePath).state
}

function saveState(filePath, state) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true })
  const tmpPath = `${filePath}.tmp`
  fs.writeFileSync(tmpPath, JSON.stringify(state, null, 2))
  fs.renameSync(tmpPath, filePath)
}

/**
 * A window is "visible" if at least `minVisiblePx` of it overlaps some
 * display's work area. Prevents restoring a window entirely off-screen
 * after a monitor is disconnected or a resolution changes (brief section 7
 * and 8's explicit requirement).
 */
function isBoundsVisible(bounds, displays, minVisiblePx = 40) {
  if (bounds.x == null || bounds.y == null) return false
  return displays.some((d) => {
    const area = d.workArea
    const overlapX = Math.min(bounds.x + bounds.width, area.x + area.width) - Math.max(bounds.x, area.x)
    const overlapY = Math.min(bounds.y + bounds.height, area.y + area.height) - Math.max(bounds.y, area.y)
    return overlapX >= minVisiblePx && overlapY >= minVisiblePx
  })
}

function overlapArea(bounds, area) {
  const ox = Math.min(bounds.x + bounds.width, area.x + area.width) - Math.max(bounds.x, area.x)
  const oy = Math.min(bounds.y + bounds.height, area.y + area.height) - Math.max(bounds.y, area.y)
  return ox > 0 && oy > 0 ? ox * oy : 0
}

/** Id of the display with the largest overlap with bounds, or null. */
function displayForBounds(bounds, displays) {
  if (!bounds || bounds.x == null || bounds.y == null) return null
  let best = null
  let bestArea = 0
  for (const d of displays) {
    const a = overlapArea(bounds, d.workArea)
    if (a > bestArea) {
      best = d.id
      bestArea = a
    }
  }
  return best
}

function fitInto(area, x, y, width, height) {
  const w = Math.min(width, area.width)
  const h = Math.min(height, area.height)
  return {
    x: Math.round(Math.min(Math.max(x, area.x), area.x + area.width - w)),
    y: Math.round(Math.min(Math.max(y, area.y), area.y + area.height - h)),
    width: Math.round(w),
    height: Math.round(h),
  }
}

/**
 * Returns bounds safe to apply: restored on the saved display (clamped into
 * its work area), kept where still visible, or centered on the primary display.
 */
function resolveBounds(saved, displays, primaryDisplay, defaults = DEFAULT_STATE.overlay) {
  const width = pickSize(saved.width, defaults.width)
  const height = pickSize(saved.height, defaults.height)
  const hasPos = isInt(saved.x) && isInt(saved.y)
  const candidate = { x: hasPos ? saved.x : null, y: hasPos ? saved.y : null, width, height }

  const own = saved.displayId != null ? displays.find((d) => d.id === saved.displayId) : null
  if (own && hasPos) {
    const fit = fitInto(own.workArea, candidate.x, candidate.y, width, height)
    const same = fit.x === candidate.x && fit.y === candidate.y && fit.width === width && fit.height === height
    return { ...fit, displayId: own.id, reason: same ? 'restored' : 'clamped' }
  }
  if (isBoundsVisible(candidate, displays)) {
    const id = displayForBounds(candidate, displays)
    const target = displays.find((d) => d.id === id)
    const fit = fitInto(target.workArea, candidate.x, candidate.y, width, height)
    const same = fit.x === candidate.x && fit.y === candidate.y && fit.width === width && fit.height === height
    return { ...fit, displayId: id, reason: same ? 'restored' : 'clamped' }
  }
  const area = primaryDisplay.workArea
  const w = Math.min(width, area.width)
  const h = Math.min(height, area.height)
  return {
    x: Math.round(area.x + (area.width - w) / 2),
    y: Math.round(area.y + (area.height - h) / 2),
    width: Math.round(w),
    height: Math.round(h),
    displayId: primaryDisplay.id ?? null,
    reason: hasPos || saved.displayId != null ? 'display-missing' : 'default',
  }
}

module.exports = {
  DEFAULT_STATE,
  sanitizeState,
  loadState,
  loadStateWithDiagnostics,
  saveState,
  isBoundsVisible,
  displayForBounds,
  resolveBounds,
}
