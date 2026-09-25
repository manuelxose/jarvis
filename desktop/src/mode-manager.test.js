'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const { createModeManager, InvalidTransitionError } = require('./mode-manager')

test('starts in the given initial mode', () => {
  const mgr = createModeManager('OVERLAY_COMPACT')
  assert.equal(mgr.getMode(), 'OVERLAY_COMPACT')
})

test('valid transition settles and notifies listeners', () => {
  const mgr = createModeManager('HIDDEN')
  const events = []
  mgr.onChange((to, from) => events.push([from, to]))
  const settle = mgr.beginTransition('OVERLAY_COMPACT')
  assert.equal(mgr.getMode(), 'HIDDEN', 'mode unchanged until settle()')
  settle()
  assert.equal(mgr.getMode(), 'OVERLAY_COMPACT')
  assert.deepEqual(events, [['HIDDEN', 'OVERLAY_COMPACT']])
})

test('rejects an undefined transition', () => {
  const mgr = createModeManager('HIDDEN')
  assert.throws(() => mgr.beginTransition('OVERLAY_EXPANDED'), InvalidTransitionError)
})

test('rejects a second transition while one is in flight', () => {
  const mgr = createModeManager('HIDDEN')
  mgr.beginTransition('OVERLAY_COMPACT')
  assert.throws(() => mgr.beginTransition('COMMAND_CENTER_WINDOWED'), /already in progress/)
})

test('every mode can reach HIDDEN and OVERLAY_COMPACT directly', () => {
  const { MODES, TRANSITIONS } = require('./mode-manager')
  for (const mode of MODES) {
    if (mode !== 'HIDDEN') assert.ok(TRANSITIONS[mode].includes('HIDDEN'), `${mode} -> HIDDEN`)
    if (mode !== 'OVERLAY_COMPACT') assert.ok(TRANSITIONS[mode].includes('OVERLAY_COMPACT'), `${mode} -> OVERLAY_COMPACT`)
  }
})
