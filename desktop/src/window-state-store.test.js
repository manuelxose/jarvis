'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { loadState, saveState, isBoundsVisible, resolveBounds, DEFAULT_STATE } = require('./window-state-store')

const PRIMARY_1080P = { id: 1, workArea: { x: 0, y: 0, width: 1920, height: 1040 } }
const SECONDARY_4K = { id: 2, workArea: { x: 1920, y: 0, width: 3840, height: 2160 } }

test('loadState returns defaults when the file does not exist', () => {
  const state = loadState(path.join(os.tmpdir(), `jarvis-window-state-missing-${Date.now()}.json`))
  assert.deepEqual(state, DEFAULT_STATE)
})

test('saveState then loadState round-trips', () => {
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'jarvis-wstate-')), 'state.json')
  const written = { ...DEFAULT_STATE, lastActiveWorkspace: 'command-center' }
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
  assert.deepEqual(resolved, saved)
})

test('resolveBounds: re-centers on primary when the saved monitor is gone (disconnect case)', () => {
  // Was positioned on the now-disconnected secondary (x starts at 1920+).
  const saved = { x: 2500, y: 300, width: 360, height: 72 }
  const resolved = resolveBounds(saved, [PRIMARY_1080P], PRIMARY_1080P)
  assert.ok(isBoundsVisible(resolved, [PRIMARY_1080P]), 're-centered bounds must be visible')
  assert.equal(resolved.width, 360)
  assert.equal(resolved.height, 72)
})
