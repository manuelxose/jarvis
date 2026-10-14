import { test } from 'node:test'
import assert from 'node:assert/strict'
import { initOverlayInterop } from '../src/shell/overlay-interop.ts'
import type { JarvisDesktopBridge } from '../src/shell/jarvis-desktop.d.ts'

function fixture() {
  const doc = new EventTarget() as EventTarget & { hidden: boolean; elementFromPoint: () => unknown }
  const host = new EventTarget() as EventTarget & { getComputedStyle: () => unknown }
  const values: boolean[] = []
  let target: unknown = null, modeCallback: (() => void) | undefined
  let connected = true, rects = true, hidden = false, visibility = 'visible'
  const region = {
    get isConnected() { return connected },
    getClientRects: () => rects ? [{}] : [],
    closest: () => hidden ? {} : null,
  }
  doc.hidden = false
  doc.elementFromPoint = () => target
  host.getComputedStyle = () => ({ visibility })
  const bridge: JarvisDesktopBridge = {
    getMode: async () => 'OVERLAY_COMPACT', requestMode() {}, moveOverlayTo() {},
    setOverlayInteractive: value => values.push(value),
    onModeChanged(callback) { modeCallback = () => callback('OVERLAY_EXPANDED'); return () => { modeCallback = undefined } },
  }
  const cleanup = initOverlayInterop(doc as unknown as Document, host as unknown as Window, bridge)
  return { doc, host, bridge, values, cleanup, move() { doc.dispatchEvent(new Event('mousemove')) },
    surface() { target = { closest: () => region } }, empty() { target = { closest: () => null } },
    nullTarget() { target = null }, mode() { modeCallback?.() },
    connected(value: boolean) { connected = value }, rects(value: boolean) { rects = value },
    hidden(value: boolean) { hidden = value }, visibility(value: string) { visibility = value },
  }
}

test('forwarded nested surface/control movement sends changed booleans only; empty root passes through', () => {
  const f = fixture(); assert.deepEqual(f.values, [false])
  f.surface(); for (let i = 0; i < 1000; i++) f.move()
  assert.deepEqual(f.values, [false, true])
  f.empty(); f.move(); f.nullTarget(); f.move()
  assert.deepEqual(f.values, [false, true, false]); f.cleanup()
})

test('hidden, inert, disconnected and zero-layout regions never capture input', () => {
  const f = fixture(); f.surface()
  for (const setter of [f.connected, f.rects]) {
    setter(false); f.move(); assert.equal(f.values.at(-1), false); setter(true)
  }
  f.hidden(true); f.move(); assert.equal(f.values.at(-1), false); f.hidden(false)
  f.visibility('hidden'); f.move(); assert.equal(f.values.at(-1), false)
  f.visibility('visible'); f.doc.hidden = true; f.move(); assert.equal(f.values.at(-1), false)
  f.doc.hidden = false; f.move(); assert.equal(f.values.at(-1), true); f.cleanup()
})

test('leave, blur, hidden, pagehide, mode change, and repeated unmount reset safely', () => {
  const f = fixture(); f.surface()
  for (const [target, name] of [[f.doc, 'mouseleave'], [f.host, 'blur'], [f.doc, 'visibilitychange'], [f.host, 'pagehide']] as const) {
    f.move(); assert.equal(f.values.at(-1), true)
    target.dispatchEvent(new Event(name)); assert.equal(f.values.at(-1), false)
    target.dispatchEvent(new Event(name)); f.move(); assert.equal(f.values.at(-1), true)
  }
  f.mode(); assert.equal(f.values.at(-1), false); f.move()
  f.cleanup(); f.cleanup(); assert.equal(f.values.at(-1), false)
  const count = f.values.length; f.move(); f.mode(); f.host.dispatchEvent(new Event('blur'))
  assert.equal(f.values.length, count)
})

test('reinitialization transfers ownership without duplicate listeners or stale cleanup', () => {
  const f = fixture(); f.surface(); f.move()
  const cleanup = initOverlayInterop(f.doc as unknown as Document, f.host as unknown as Window, f.bridge)
  assert.deepEqual(f.values, [false, true, false, false])
  f.cleanup(); f.move(); assert.deepEqual(f.values, [false, true, false, false, true])
  f.mode(); assert.equal(f.values.at(-1), false)
  cleanup(); cleanup(); const count = f.values.length; f.move()
  assert.equal(f.values.length, count)
})

test('browser without bridge installs no callbacks', () => {
  const doc = { addEventListener() { throw new Error('unexpected listener') } }
  const cleanup = initOverlayInterop(doc as unknown as Document, {} as Window, undefined)
  cleanup(); cleanup()
})
