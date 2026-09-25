export interface JarvisDesktopBridge {
  getMode(): Promise<string>
  requestMode(mode: string): void
  onModeChanged(callback: (mode: string) => void): () => void
  setOverlayInteractive(interactive: boolean): void
  moveOverlayTo(x: number, y: number): void
}

declare global {
  interface Window {
    /** Present only inside the Electron shell (see desktop/src/preload.js). Absent in the browser design-review harness. */
    jarvisDesktop?: JarvisDesktopBridge
  }
}
