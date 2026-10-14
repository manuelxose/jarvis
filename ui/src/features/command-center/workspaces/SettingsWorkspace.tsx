import { useEffect, useRef, useState } from 'react'
import type { DesktopSettings } from '../../../shell/jarvis-desktop'
import { Panel } from '../../../components/layout/Panel'
import { Badge } from '../../../components/foundations/Badge'
import { Button } from '../../../components/foundations/Button'

export function SettingsWorkspace() {
  const bridge = window.jarvisDesktop?.settings
  const [settings, setSettings] = useState<DesktopSettings | null>(null)
  const [pending, setPending] = useState(false)
  const [message, setMessage] = useState('')
  const active = useRef(false)
  const busy = useRef(false)

  useEffect(() => {
    if (!bridge) return
    active.current = true
    let live = true, notified = false, expired = false
    const unavailable = () => { if (live && !notified) setMessage('Desktop settings unavailable. Try reopening Settings.') }
    const timer = window.setTimeout(() => { expired = true; if (!notified) unavailable() }, 2000)
    let unsubscribe: (() => void) | undefined
    try {
      unsubscribe = bridge.onChanged(value => { if (live) { notified = true; setSettings(value); setMessage('') } })
      void bridge.get().then(value => {
        if (!live || notified || expired) return
        if (value) { setSettings(value); setMessage('') } else unavailable()
      }).catch(unavailable).finally(() => window.clearTimeout(timer))
    } catch { queueMicrotask(unavailable); window.clearTimeout(timer) }
    return () => { live = false; active.current = false; window.clearTimeout(timer); unsubscribe?.() }
  }, [bridge])

  async function toggleLogin() {
    if (!bridge || !settings || busy.current) return
    busy.current = true
    setPending(true)
    setMessage('')
    // Bound the wait; never display an optimistic OS preference.
    let timer: number | undefined
    try {
      const result = await Promise.race([
        bridge.setLaunchAtLogin(!settings.launchAtLogin),
        new Promise<null>(resolve => { timer = window.setTimeout(() => resolve(null), 2000) }),
      ])
      if (!active.current) return
      if (result) {
        setSettings(result)
        setMessage(result.launchAtLogin !== settings.launchAtLogin ? 'Desktop sign-in preference saved.' : 'Sign-in preference unchanged. Check desktop bootstrap logs.')
      } else setMessage('Desktop sign-in preference unavailable. Try again.')
    } catch { if (active.current) setMessage('Desktop sign-in preference unavailable. Try again.') }
    finally { window.clearTimeout(timer); busy.current = false; if (active.current) setPending(false) }
  }

  return (
    <>
    {bridge && <Panel title="Desktop">
      <Button type="button" role="switch" aria-checked={settings?.launchAtLogin ?? false} disabled={!settings || pending} onClick={() => { void toggleLogin() }}>
        Launch Jarvis desktop at sign-in
      </Button>
      <p>Default off. When enabled, Jarvis desktop starts hidden in the tray.</p>
      {settings && <ul>{settings.shortcuts.map(shortcut => <li key={shortcut.action}>
        {shortcut.action === 'toggleOverlay' ? 'Toggle overlay' : 'Toggle Command Center'}: <code>{shortcut.accelerator}</code>{' '}
        <Badge tone={shortcut.registered ? 'info' : undefined}>{shortcut.registered ? 'Registered' : 'Unavailable'}</Badge>
      </li>)}</ul>}
      <p>Shortcuts are read-only here. Configure them in <code>window-state.json</code> in the desktop userData directory, then restart the desktop.</p>
      {message && <p role="status">{message}</p>}
    </Panel>}
    <Panel title="Appearance">
      <p style={{ fontSize: 'var(--text-sm)', color: 'var(--text-secondary)', maxWidth: 480 }}>
        Jarvis honours the OS <code>prefers-reduced-motion</code> setting automatically — all motion tokens
        collapse to 0ms. There is one design direction and one density this phase (see{' '}
        <code>docs/design/jarvis-design-system.md</code>); theme switching and density presets are deferred
        to a later phase, not built speculatively here.
      </p>
      <div style={{ display: 'flex', gap: 'var(--space-2)', marginTop: 'var(--space-3)' }}>
        <Badge tone="info">Direction: Adaptive Command</Badge>
        <Badge>Density: standard</Badge>
      </div>
    </Panel>
    </>
  )
}
