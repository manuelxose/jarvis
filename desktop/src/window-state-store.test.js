'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { loadState, loadStateWithDiagnostics, sanitizeState, saveState, isBoundsVisible, displayForBounds, resolveBounds, DEFAULT_STATE } = require('./window-state-store')

const PRIMARY_1080P = { id: 1, workArea: { x: 0, y: 0, width: 1920, height: 1040 } }
const SECONDARY_4K = { id: 2, workArea: { x: 1920, y: 0, width: 3840, height: 2160 } }

test('loadState returns defaults when the file does not exist', () => {
  const state = loadState(path.join(os.tmpdir(), `jarvis-window-state-missing-${Date.now()}.json`))
  assert.deepEqual(state, DEFAULT_STATE)
})

test('saveState then loadState round-trips', () => {
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'jarvis-wstate-')), 'state.json')
  const written = { ...DEFAULT_STATE, lastActiveWorkspace: 'COMMAND_CENTER_WINDOWED' }
  saveState(file, written)
  assert.deepEqual(loadState(file), written)
})

test('isBoundsVisible: fully on-screen bounds are visible', () => {
  const bounds = { x: 100, y: 100, width: 360, height: 72 }
  assert.ok(isBoundsVisible(bounds, [PRIMARY_1080P]))
})

test('isBoundsVisible: bounds entirely off every display are not visible', () => {
  const bounds = { x: 9999, y: 9999, width: 360, height: 72 }
  assert.ok(!isBoundsVisible(bounds, [PRIMARY_1080P, SECONDARY_4K]))
})

test('resolveBounds: keeps saved position when the monitor is still connected', () => {
  const saved = { x: 200, y: 150, width: 360, height: 72 }
  const resolved = resolveBounds(saved, [PRIMARY_1080P, SECONDARY_4K], PRIMARY_1080P)
  assert.deepEqual(resolved, { ...saved, displayId: 1, reason: 'restored' })
})

test('resolveBounds: re-centers on primary when the saved monitor is gone (disconnect case)', () => {
  // Was positioned on the now-disconnected secondary (x starts at 1920+).
  const saved = { x: 2500, y: 300, width: 360, height: 72 }
  const resolved = resolveBounds(saved, [PRIMARY_1080P], PRIMARY_1080P)
  assert.ok(isBoundsVisible(resolved, [PRIMARY_1080P]), 're-centered bounds must be visible')
  assert.equal(resolved.width, 360)
  assert.equal(resolved.height, 72)
})

const LEFT = { id: 3, workArea: { x: -1280, y: 0, width: 1280, height: 984 } }
const HIDPI = { id: 4, workArea: { x: 0, y: 0, width: 2560, height: 1400 } } // 4K @150% in DIPs
const tmpFile = (content) => {
  const f = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'jarvis-wstate-')), 'state.json')
  if (content !== undefined) fs.writeFileSync(f, content)
  return f
}

test('resolveBounds: restores on a secondary display at negative x by displayId', () => {
  const r = resolveBounds({ displayId: 3, x: -1000, y: 100, width: 360, height: 72 }, [PRIMARY_1080P, LEFT], PRIMARY_1080P)
  assert.deepEqual(r, { x: -1000, y: 100, width: 360, height: 72, displayId: 3, reason: 'restored' })
})

test('resolveBounds: disconnected display with off-screen coords centers on primary (display-missing)', () => {
  const r = resolveBounds({ displayId: 3, x: -1000, y: 100, width: 360, height: 72 }, [PRIMARY_1080P], PRIMARY_1080P)
  assert.equal(r.reason, 'display-missing')
  assert.equal(r.displayId, 1)
  assert.ok(isBoundsVisible(r, [PRIMARY_1080P]))
})

test('resolveBounds: resolution shrink clamps into the same display', () => {
  const shrunk = { id: 4, workArea: { x: 0, y: 0, width: 1280, height: 700 } }
  const r = resolveBounds({ displayId: 4, x: 2000, y: 1200, width: 1000, height: 600 }, [shrunk], PRIMARY_1080P)
  assert.equal(r.reason, 'clamped')
  assert.equal(r.displayId, 4)
  assert.deepEqual([r.x, r.y, r.width, r.height], [280, 100, 1000, 600])
})

test('resolveBounds: oversize window is clamped to the work area', () => {
  const r = resolveBounds({ displayId: 1, x: 0, y: 0, width: 5000, height: 4000 }, [PRIMARY_1080P], PRIMARY_1080P)
  assert.deepEqual([r.width, r.height], [1920, 1040])
  const c = resolveBounds({ x: null, y: null, width: 5000, height: 4000 }, [PRIMARY_1080P], PRIMARY_1080P)
  assert.deepEqual([c.x, c.y, c.width, c.height], [0, 0, 1920, 1040])
})

test('resolveBounds: HiDPI display restores in DIPs', () => {
  const r = resolveBounds({ displayId: 4, x: 100, y: 100, width: 1280, height: 800 }, [PRIMARY_1080P, HIDPI], PRIMARY_1080P)
  assert.equal(r.reason, 'restored')
})

test('resolveBounds: command center defaults are used for center fallback', () => {
  const r = resolveBounds({ x: null, y: null, width: null, height: undefined }, [PRIMARY_1080P], PRIMARY_1080P, DEFAULT_STATE.commandCenter)
  assert.deepEqual([r.width, r.height, r.reason], [1280, 800, 'default'])
  assert.deepEqual([r.x, r.y], [320, 120])
})

test('displayForBounds: largest overlap wins; null when none', () => {
  assert.equal(displayForBounds({ x: 1800, y: 0, width: 400, height: 100 }, [PRIMARY_1080P, SECONDARY_4K]), 2)
  assert.equal(displayForBounds({ x: 50000, y: 0, width: 4, height: 4 }, [PRIMARY_1080P]), null)
})

test('sanitizeState: every corrupt field falls back to defaults', () => {
  const s = sanitizeState({
    initialMode: 'BOGUS',
    overlay: { x: 'a', y: 1e9, width: -5, height: 99999, displayId: 'x', evil: 1 },
    commandCenter: 'nope',
    fullscreenPreference: 'yes',
    lastActiveWorkspace: 'command-center',
    launchAtLogin: 1,
    extra: true,
  })
  assert.deepEqual(s, DEFAULT_STATE)
  assert.deepEqual(sanitizeState({ commandCenter: { maximized: 'x', width: 900 } }).commandCenter, { ...DEFAULT_STATE.commandCenter, width: 900 })
  assert.equal(sanitizeState({ initialMode: 'HIDDEN' }).initialMode, 'HIDDEN')
  assert.deepEqual(sanitizeState(undefined), DEFAULT_STATE)
})

test('loadStateWithDiagnostics: corrupt JSON and non-object JSON recover to defaults', () => {
  const bad = tmpFile('{not json')
  assert.deepEqual(loadStateWithDiagnostics(bad), { state: DEFAULT_STATE, recovered: 'corrupt-file', shortcutRejections: [] })
  assert.equal(fs.readFileSync(bad, 'utf8'), '{not json')
  assert.equal(loadStateWithDiagnostics(tmpFile('[1,2]')).recovered, 'corrupt-file')
  assert.equal(loadStateWithDiagnostics(path.join(os.tmpdir(), `nope-${Date.now()}.json`)).recovered, null)
})

test('loadState sanitizes a hand-edited initialMode', () => {
  assert.equal(loadState(tmpFile('{"initialMode":"x"}')).initialMode, DEFAULT_STATE.initialMode)
})

test('round trip preserves displayId', () => {
  const file = tmpFile()
  const w = { ...DEFAULT_STATE, overlay: { ...DEFAULT_STATE.overlay, displayId: 3, x: -1000, y: 50 } }
  saveState(file, w)
  assert.deepEqual(loadState(file), w)
})

test('defaults include shortcuts and launchAtLogin false', () => {
  assert.deepEqual(DEFAULT_STATE.shortcuts, { toggleOverlay: 'Control+Alt+Shift+J', toggleCommandCenter: 'Control+Alt+Shift+C' })
  assert.equal(DEFAULT_STATE.launchAtLogin, false)
})

test('corrupt shortcuts fall back to defaults and are reported', () => {
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'jarvis-wstate-')), 'state.json')
  fs.writeFileSync(file, JSON.stringify({ shortcuts: { toggleOverlay: 'Control+Alt+J', toggleCommandCenter: 42 } }))
  const { state, shortcutRejections, recovered } = loadStateWithDiagnostics(file)
  assert.deepEqual(state.shortcuts, DEFAULT_STATE.shortcuts)
  assert.equal(recovered, null)
  assert.deepEqual(shortcutRejections.map((r) => [r.action, r.reason]), [['toggleOverlay', 'reserved'], ['toggleCommandCenter', 'invalid']])
  assert.deepEqual(sanitizeState({ shortcuts: 'nope' }).shortcuts, DEFAULT_STATE.shortcuts)
})

test('non-boolean launchAtLogin becomes false', () => {
  assert.equal(sanitizeState({ launchAtLogin: 'yes' }).launchAtLogin, false)
  assert.equal(sanitizeState({ launchAtLogin: 1 }).launchAtLogin, false)
  assert.equal(sanitizeState({ launchAtLogin: true }).launchAtLogin, true)
})

test('custom shortcuts and launchAtLogin round-trip', () => {
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'jarvis-wstate-')), 'state.json')
  const written = { ...DEFAULT_STATE, launchAtLogin: true, shortcuts: { toggleOverlay: 'Control+Shift+F9', toggleCommandCenter: 'Alt+Shift+K' } }
  saveState(file, written)
  assert.deepEqual(loadState(file), written)
  assert.deepEqual(loadStateWithDiagnostics(file).shortcutRejections, [])
  assert.deepEqual(loadStateWithDiagnostics(path.join(path.dirname(file), 'missing.json')).shortcutRejections, [])
})
