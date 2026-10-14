import type { JarvisDesktopBridge } from './jarvis-desktop'

const owners = new WeakMap<Document, () => void>()

/** Explicit visible regions only; forwarded movement is not native receipt proof. */
export function initOverlayInterop(
  doc: Document = document,
  host: Window = window,
  bridge: JarvisDesktopBridge | undefined = host.jarvisDesktop,
): () => void {
  owners.get(doc)?.()
  if (!bridge) return () => {}
  let last: boolean | undefined
  let disposed = false
  function send(value: boolean) {
    if (value === last) return
    last = value
    bridge!.setOverlayInteractive(value)
  }
  const reset = () => send(false)
  function move(event: MouseEvent) {
    const target = doc.elementFromPoint(event.clientX, event.clientY)
    const region = target?.closest('[data-overlay-interactive]')
    send(!doc.hidden && Boolean(region?.isConnected && region.getClientRects().length &&
      !region.closest('[hidden], [inert], [aria-hidden="true"]') &&
      host.getComputedStyle(region).visibility === 'visible'))
  }
  reset()
  doc.addEventListener('mousemove', move)
  doc.addEventListener('mouseleave', reset)
  doc.addEventListener('visibilitychange', reset)
  host.addEventListener('blur', reset)
  host.addEventListener('pagehide', reset)
  // Native mode/hide resets can precede the next forwarded mouse event.
  const unsubscribe = bridge.onModeChanged(reset)
  const cleanup = () => {
    if (disposed) return
    disposed = true
    doc.removeEventListener('mousemove', move)
    doc.removeEventListener('mouseleave', reset)
    doc.removeEventListener('visibilitychange', reset)
    host.removeEventListener('blur', reset)
    host.removeEventListener('pagehide', reset)
    unsubscribe()
    reset()
    if (owners.get(doc) === cleanup) owners.delete(doc)
  }
  owners.set(doc, cleanup)
  return cleanup
}
