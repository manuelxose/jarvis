'use strict'

// Pure accelerator validation for the desktop global shortcuts. No Electron,
// no fs, and it must not require window-state-store (which requires this).

const SHORTCUT_ACTIONS = Object.freeze(['toggleOverlay', 'toggleCommandCenter'])

const DEFAULT_SHORTCUTS = Object.freeze({
  toggleOverlay: 'Control+Alt+Shift+J',
  toggleCommandCenter: 'Control+Alt+Shift+C',
})

// Normalized accelerators that must never be taken: Core's daemon hotkey
// (ctrl+alt+j, config.win.json) and VS Code defaults reachable by the grammar
// below. Combos with named keys (Alt+Tab, Alt+F4, Ctrl+Alt+Delete, arrows,
// backtick, Escape) or a single modifier are already rejected as 'invalid'.
const RESERVED = Object.freeze([
  'Control+Alt+J',
  'Control+Shift+P',
  'Control+Shift+E',
  'Control+Shift+F',
  'Control+Shift+G',
  'Control+Shift+D',
  'Control+Shift+X',
  'Control+Shift+N',
  'Control+Shift+T',
])

const MODIFIERS = { control: 'Control', ctrl: 'Control', commandorcontrol: 'Control', cmdorctrl: 'Control', alt: 'Alt', shift: 'Shift' }
const MODIFIER_ORDER = ['Control', 'Alt', 'Shift']
const KEY_RE = /^(?:[A-Z0-9]|F(?:[1-9]|1[0-9]|2[0-4]))$/

function normalizeAccelerator(value) {
  if (typeof value !== 'string' || value.length === 0 || value.length > 64) return null
  const mods = new Set()
  let key = null
  for (const raw of value.split('+')) {
    const token = raw.trim()
    if (!token) return null
    const mod = MODIFIERS[token.toLowerCase()]
    if (mod) {
      mods.add(mod)
      continue
    }
    const upper = token.toUpperCase()
    if (key !== null || !KEY_RE.test(upper)) return null
    key = upper
  }
  if (key === null) return null
  const isFunctionKey = key.length > 1
  if (isFunctionKey ? mods.size < 1 : mods.size < 2) return null
  return [...MODIFIER_ORDER.filter((m) => mods.has(m)), key].join('+')
}

const RESERVED_SET = new Set(RESERVED.map((a) => a.toLowerCase()))
const isReserved = (normalized) => RESERVED_SET.has(normalized.toLowerCase())

function sanitizeShortcuts(raw) {
  const input = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {}
  const shortcuts = {}
  const rejected = []
  const taken = new Set()
  for (const action of SHORTCUT_ACTIONS) {
    const value = input[action]
    let chosen = DEFAULT_SHORTCUTS[action]
    if (value !== undefined) {
      const normalized = normalizeAccelerator(value)
      let reason = null
      if (normalized === null) reason = 'invalid'
      else if (isReserved(normalized)) reason = 'reserved'
      else if (taken.has(normalized)) reason = 'duplicate'
      if (reason) rejected.push({ action, value, reason })
      else chosen = normalized
    }
    shortcuts[action] = chosen
    taken.add(chosen)
  }
  // A default may collide with an earlier custom choice; restore all defaults.
  if (taken.size !== SHORTCUT_ACTIONS.length) {
    for (const action of SHORTCUT_ACTIONS) {
      if (shortcuts[action] !== DEFAULT_SHORTCUTS[action]) {
        rejected.push({ action, value: shortcuts[action], reason: 'duplicate' })
      }
      shortcuts[action] = DEFAULT_SHORTCUTS[action]
    }
  }
  return { shortcuts, rejected }
}

module.exports = { SHORTCUT_ACTIONS, DEFAULT_SHORTCUTS, RESERVED, normalizeAccelerator, sanitizeShortcuts }
