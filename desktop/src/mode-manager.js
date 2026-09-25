'use strict'

// Mode state machine for the Jarvis desktop shell. Pure JS, no Electron
// dependency, so it is unit-testable without a display. See
// docs/architecture/window-state-machine.md for the diagram this encodes.

const MODES = Object.freeze([
  'HIDDEN',
  'OVERLAY_COMPACT',
  'OVERLAY_EXPANDED',
  'COMMAND_CENTER_WINDOWED',
  'COMMAND_CENTER_FULLSCREEN',
])

// Adjacency list of allowed transitions. Every mode can always go to HIDDEN
// (panic/tray "hide" must never be blocked) and every mode can always return
// to OVERLAY_COMPACT (the "return to overlay" affordance required by the
// brief, section 6/12).
const TRANSITIONS = {
  HIDDEN: ['OVERLAY_COMPACT', 'COMMAND_CENTER_WINDOWED'],
  OVERLAY_COMPACT: ['HIDDEN', 'OVERLAY_EXPANDED', 'COMMAND_CENTER_WINDOWED'],
  OVERLAY_EXPANDED: ['HIDDEN', 'OVERLAY_COMPACT', 'COMMAND_CENTER_WINDOWED'],
  COMMAND_CENTER_WINDOWED: ['HIDDEN', 'OVERLAY_COMPACT', 'COMMAND_CENTER_FULLSCREEN'],
  COMMAND_CENTER_FULLSCREEN: ['HIDDEN', 'OVERLAY_COMPACT', 'COMMAND_CENTER_WINDOWED'],
}

class InvalidTransitionError extends Error {
  constructor(from, to) {
    super(`Invalid mode transition: ${from} -> ${to}`)
    this.name = 'InvalidTransitionError'
    this.from = from
    this.to = to
  }
}

/**
 * Creates a mode manager. `transitioning` guards against overlapping
 * transitions (brief section 7: "Avoid simultaneous conflicting mode
 * transitions") — a caller must settle() the in-flight transition before
 * starting another.
 */
function createModeManager(initialMode = 'HIDDEN') {
  if (!MODES.includes(initialMode)) {
    throw new Error(`Unknown initial mode: ${initialMode}`)
  }

  let current = initialMode
  let transitioning = false
  const listeners = new Set()

  function getMode() {
    return current
  }

  function isTransitioning() {
    return transitioning
  }

  function canTransition(to) {
    return MODES.includes(to) && TRANSITIONS[current].includes(to)
  }

  /**
   * Begins a transition. Returns a `settle()` function the caller must
   * invoke once the underlying window operations complete (or throw); the
   * mode only becomes current, and listeners only fire, on settle.
   */
  function beginTransition(to) {
    if (transitioning) {
      throw new Error(`Transition already in progress: ${current} -> (pending)`)
    }
    if (!canTransition(to)) {
      throw new InvalidTransitionError(current, to)
    }
    const from = current
    transitioning = true
    let settled = false
    return function settle() {
      if (settled) return
      settled = true
      transitioning = false
      current = to
      for (const listener of listeners) listener(to, from)
    }
  }

  function onChange(listener) {
    listeners.add(listener)
    return () => listeners.delete(listener)
  }

  return {
    getMode,
    isTransitioning,
    canTransition,
    beginTransition,
    onChange,
  }
}

module.exports = { createModeManager, MODES, TRANSITIONS, InvalidTransitionError }
