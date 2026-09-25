'use strict'

const fs = require('node:fs')
const path = require('node:path')

// Persisted window/display preferences (brief section 7 + 13). Pure
// functions apart from the fs calls in load/save, so the coordinate-math
// (the part that actually breaks on a disconnected monitor) is unit-testable
// without touching disk or Electron's `screen` module.

const DEFAULT_STATE = Object.freeze({
  initialMode: 'OVERLAY_COMPACT',
  overlay: { displayId: null, x: null, y: null, width: 360, height: 72 },
  commandCenter: { displayId: null, x: null, y: null, width: 1280, height: 800, maximized: false },
  fullscreenPreference: false,
  lastActiveWorkspace: null,
  launchAtLogin: false,
})

function loadState(filePath) {
  try {
    const raw = fs.readFileSync(filePath, 'utf8')
    const parsed = JSON.parse(raw)
    return { ...DEFAULT_STATE, ...parsed }
  } catch (err) {
    if (err.code !== 'ENOENT') {
      console.error('window-state-store: failed to read state, using defaults', err)
    }
    return { ...DEFAULT_STATE }
  }
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

/**
 * Returns bounds safe to apply: the saved bounds if still visible on the
 * given displays, otherwise centered on the primary display's work area.
 */
function resolveBounds(saved, displays, primaryDisplay) {
  const width = saved.width || DEFAULT_STATE.overlay.width
  const height = saved.height || DEFAULT_STATE.overlay.height
  const candidate = { x: saved.x, y: saved.y, width, height }
  if (isBoundsVisible(candidate, displays)) {
    return candidate
  }
  const area = primaryDisplay.workArea
  return {
    x: Math.round(area.x + (area.width - width) / 2),
    y: Math.round(area.y + (area.height - height) / 2),
    width,
    height,
  }
}

module.exports = { DEFAULT_STATE, loadState, saveState, isBoundsVisible, resolveBounds }
