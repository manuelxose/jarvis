'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const { SHORTCUT_ACTIONS, DEFAULT_SHORTCUTS, RESERVED, normalizeAccelerator, sanitizeShortcuts } = require('./shortcuts')

test('normalizes case, aliases and modifier order', () => {
  assert.equal(normalizeAccelerator('shift+alt+ctrl+j'), 'Control+Alt+Shift+J')
  assert.equal(normalizeAccelerator('CommandOrControl+Shift+k'), 'Control+Shift+K')
  assert.equal(normalizeAccelerator(' Ctrl + Alt + 7 '), 'Control+Alt+7')
  assert.equal(normalizeAccelerator('Alt+f12'), 'Alt+F12')
  assert.equal(normalizeAccelerator('Control+F24'), 'Control+F24')
})

test('rejects invalid shapes', () => {
  for (const v of ['J', 'Control+J', 'Shift+F5x', 'F5', 'Control+F25', 'Control+F0', 'Control+Alt+', '+Control+Alt+J',
    'Control+Alt+J+K', 'Control+Alt+Tab', 'Control+Alt+`', 'Control+Alt+Delete', 'Control+Alt', '', 'Control++J']) {
    assert.equal(normalizeAccelerator(v), null, v)
  }
})

test('rejects Super/Meta/Windows key', () => {
  for (const v of ['Super+Alt+J', 'Meta+Control+J', 'Win+Control+J', 'Control+Alt+Super+J', 'Command+Control+J']) {
    assert.equal(normalizeAccelerator(v), null, v)
  }
})

test('rejects non-string, garbage and over-long input', () => {
  for (const v of [undefined, null, 42, {}, [], true, '\u0000', 'Control+Alt+J'.padEnd(65, '+')]) {
    assert.equal(normalizeAccelerator(v), null)
  }
  assert.equal(normalizeAccelerator(`${'Control+'.repeat(9)}J`), null)
})

test('defaults are valid, unique and not reserved', () => {
  for (const a of SHORTCUT_ACTIONS) assert.equal(normalizeAccelerator(DEFAULT_SHORTCUTS[a]), DEFAULT_SHORTCUTS[a])
  assert.notEqual(DEFAULT_SHORTCUTS.toggleOverlay, DEFAULT_SHORTCUTS.toggleCommandCenter)
  for (const r of RESERVED) assert.ok(!Object.values(DEFAULT_SHORTCUTS).includes(r))
  const clean = sanitizeShortcuts(DEFAULT_SHORTCUTS)
  assert.deepEqual(clean, { shortcuts: DEFAULT_SHORTCUTS, rejected: [] })
})

test('Core daemon hotkey ctrl+alt+j is reserved in any casing or order', () => {
  for (const v of ['ctrl+alt+j', 'Alt+Control+J', 'CTRL+ALT+j', 'CommandOrControl+Alt+J']) {
    const { shortcuts, rejected } = sanitizeShortcuts({ toggleOverlay: v })
    assert.equal(shortcuts.toggleOverlay, DEFAULT_SHORTCUTS.toggleOverlay, v)
    assert.deepEqual(rejected, [{ action: 'toggleOverlay', value: v, reason: 'reserved' }])
  }
})

test('VS Code defaults are reserved', () => {
  const { rejected } = sanitizeShortcuts({ toggleOverlay: 'shift+ctrl+p', toggleCommandCenter: 'Control+Shift+E' })
  assert.deepEqual(rejected.map((r) => r.reason), ['reserved', 'reserved'])
})

test('duplicates across actions: later action falls back', () => {
  const { shortcuts, rejected } = sanitizeShortcuts({ toggleOverlay: 'Control+Shift+F9', toggleCommandCenter: 'shift+ctrl+f9' })
  assert.equal(shortcuts.toggleOverlay, 'Control+Shift+F9')
  assert.equal(shortcuts.toggleCommandCenter, DEFAULT_SHORTCUTS.toggleCommandCenter)
  assert.deepEqual(rejected.map((r) => [r.action, r.reason]), [['toggleCommandCenter', 'duplicate']])
})

test('custom choice colliding with the other default resets to defaults', () => {
  const { shortcuts, rejected } = sanitizeShortcuts({ toggleOverlay: DEFAULT_SHORTCUTS.toggleCommandCenter })
  assert.deepEqual(shortcuts, DEFAULT_SHORTCUTS)
  assert.ok(rejected.some((r) => r.reason === 'duplicate'))
})

test('garbage containers fall back to defaults without rejections', () => {
  for (const raw of [undefined, null, 'x', 5, [], [1]]) {
    assert.deepEqual(sanitizeShortcuts(raw), { shortcuts: DEFAULT_SHORTCUTS, rejected: [] })
  }
})

test('unknown actions are ignored', () => {
  assert.deepEqual(Object.keys(sanitizeShortcuts({ evil: 'Control+Alt+Z' }).shortcuts), SHORTCUT_ACTIONS)
})
