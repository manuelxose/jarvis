'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const { buildTrayMenu, trayIcon, TRAY_ICON } = require('./tray')

const shortcuts = { toggleOverlay: 'Control+Alt+Shift+J', toggleCommandCenter: 'Control+Alt+Shift+C' }
const actionsLog = []
const actions = new Proxy({}, { get: (_t, name) => (...args) => actionsLog.push([name, ...args]) })
const labels = menu => menu.map(i => i.label)

test('menu distinguishes exit desktop from stopping Core and gates Stop on a connected Core', () => {
  const connected = buildTrayMenu({ mode: 'OVERLAY_COMPACT', launchAtLogin: false, shortcuts, shortcutStatus: {}, coreState: 'connected' }, actions)
  assert.ok(labels(connected).includes('Exit desktop (Jarvis keeps running)'))
  assert.equal(connected.find(i => i.label === 'Stop Jarvis Core…').enabled, true)
  const down = buildTrayMenu({ mode: 'HIDDEN', launchAtLogin: false, shortcuts, shortcutStatus: {}, coreState: 'disconnected' }, actions)
  assert.equal(down.find(i => i.label === 'Stop Jarvis Core…').enabled, false)
})

test('overlay label follows mode; launch checkbox reflects state and reports the toggled value', () => {
  assert.ok(labels(buildTrayMenu({ mode: 'OVERLAY_EXPANDED', shortcuts }, actions)).includes('Hide overlay'))
  assert.ok(labels(buildTrayMenu({ mode: 'COMMAND_CENTER_WINDOWED', shortcuts }, actions)).includes('Show overlay'))
  const item = buildTrayMenu({ mode: 'HIDDEN', launchAtLogin: true, shortcuts }, actions).find(i => i.label === 'Launch at sign-in')
  assert.equal(item.type, 'checkbox'); assert.equal(item.checked, true)
  actionsLog.length = 0
  item.click({ checked: false })
  assert.deepEqual(actionsLog, [['setLaunchAtLogin', false]])
})

test('shortcut lines are disabled and flag unavailable registrations', () => {
  const menu = buildTrayMenu({ mode: 'HIDDEN', shortcuts, shortcutStatus: { toggleOverlay: 'registered', toggleCommandCenter: 'unavailable' } }, actions)
  const lines = menu.filter(i => /^Toggle /.test(i.label || ''))
  assert.equal(lines.length, 2)
  assert.ok(lines.every(i => i.enabled === false))
  assert.equal(lines[0].label.includes('unavailable'), false)
  assert.equal(lines[1].label.endsWith('(unavailable)'), true)
})

test('trayIcon loads the committed PNG with 16px and 32px representations', () => {
  const png = fs.readFileSync(TRAY_ICON)
  assert.equal(png.subarray(1, 4).toString(), 'PNG')
  assert.equal(png.readUInt32BE(16), 32)
  assert.equal(png.readUInt32BE(20), 32)
  let loaded, representation
  const sizes = []
  const small = { addRepresentation: rep => { representation = rep } }
  const image = { resize: s => { sizes.push(s); return s.width === 16 ? small : { toPNG: () => png } } }
  assert.equal(trayIcon({ createFromPath: p => { loaded = p; return image } }), small)
  assert.equal(loaded, TRAY_ICON)
  assert.deepEqual(sizes, [{ width: 16, height: 16 }, { width: 32, height: 32 }])
  assert.deepEqual(representation, { scaleFactor: 2, buffer: png })
})
