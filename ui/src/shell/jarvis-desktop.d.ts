import type { JarvisEvent } from '../protocol/events'

export type DesktopMode = 'HIDDEN' | 'OVERLAY_COMPACT' | 'OVERLAY_EXPANDED' | 'COMMAND_CENTER_WINDOWED' | 'COMMAND_CENTER_FULLSCREEN'

export type CoreConnectionState = 'connecting' | 'connected' | 'reconnecting' | 'disconnected' | 'auth-failure' | 'incompatible-version'

export interface JarvisCoreBridge {
  getState(): Promise<CoreConnectionState | null>
  onState(callback: (state: CoreConnectionState) => void): () => void
  onEvent(callback: (event: JarvisEvent) => void): () => void
  /** Resolves with ok:false and a short error code; never throws. The Core token never reaches the renderer. */
  control(action: 'activate' | 'sleep'): Promise<{ ok: true } | { ok: false; error: string }>
}

export interface DesktopSettings {
  launchAtLogin: boolean
  shortcuts: { action: 'toggleOverlay' | 'toggleCommandCenter'; accelerator: string; registered: boolean }[]
}

export interface DesktopSettingsBridge {
  get(): Promise<DesktopSettings | null>
  setLaunchAtLogin(value: boolean): Promise<DesktopSettings | null>
  onChanged(callback: (settings: DesktopSettings) => void): () => void
  onOpenRequested(callback: () => void): () => void
}

export interface JarvisDesktopBridge {
  /** Initial snapshot; a newer onModeChanged notification takes precedence. */
  getMode(): Promise<DesktopMode | null>
  /** Fire-and-forget request, not native transition confirmation. */
  requestMode(mode: DesktopMode): void
  /** Native mode notifications. Dispose the subscription on renderer unmount. */
  onModeChanged(callback: (mode: DesktopMode) => void): () => void
  setOverlayInteractive(interactive: boolean): void
  moveOverlayTo(x: number, y: number): void
  /** Optional so older harness fakes without a Core bridge still type-check. */
  core?: JarvisCoreBridge
  /** Desktop preferences only; shortcut accelerators are read-only. */
  settings?: DesktopSettingsBridge
}

declare global {
  interface Window {
    /** Present only inside the Electron shell (see desktop/src/preload.js). Absent in the browser design-review harness. */
    jarvisDesktop?: JarvisDesktopBridge
  }
}
