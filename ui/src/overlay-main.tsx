import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { Overlay } from './features/overlay/Overlay.tsx'
import { initOverlayInterop } from './shell/overlay-interop.ts'

const rootElement = document.getElementById('root')
if (!rootElement) {
  throw new Error('root element not found')
}

if (window.jarvisDesktop) {
  document.documentElement.dataset.desktopShell = 'electron'
  initOverlayInterop()
}

createRoot(rootElement).render(
  <StrictMode>
    <Overlay />
  </StrictMode>,
)
