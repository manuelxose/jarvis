/**
 * Wires the transparent overlay window's click-through behavior when running
 * inside the Electron shell (brief section 5, "critical requirement:
 * click-through"). No-op in the browser design-review harness, where
 * `window.jarvisDesktop` doesn't exist.
 *
 * How it works: the main process starts the overlay window in
 * `setIgnoreMouseEvents(true, { forward: true })`, so mouse events pass
 * through to whatever is underneath *and* are still forwarded to this
 * renderer for hit-testing. On every forwarded mousemove we ask the DOM what
 * is actually at that point: if it's the overlay's own background element
 * (marked `data-overlay-root`) or nothing, the point is empty space and the
 * window should keep passing clicks through; if it's anything nested inside
 * (a button, the conversation panel, ...), the window should capture mouse
 * input so that element is actually clickable.
 */
export function initOverlayInterop(): () => void {
  const bridge = window.jarvisDesktop
  if (!bridge) return () => {}

  let lastInteractive: boolean | null = null

  function handleMouseMove(event: MouseEvent) {
    const target = document.elementFromPoint(event.clientX, event.clientY)
    const interactive = target !== null && !target.hasAttribute('data-overlay-root')
    if (interactive !== lastInteractive) {
      lastInteractive = interactive
      bridge!.setOverlayInteractive(interactive)
    }
  }

  document.addEventListener('mousemove', handleMouseMove)
  return () => document.removeEventListener('mousemove', handleMouseMove)
}
