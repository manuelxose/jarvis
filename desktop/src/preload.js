'use strict'

const { contextBridge, ipcRenderer } = require('electron')

// The entire native surface exposed to the renderer. No `require`, no
// arbitrary IPC channel, no shell/filesystem access — section 4 and 14's
// "frontend must never obtain unrestricted access to the operating system"
// enforced here, not by convention.
contextBridge.exposeInMainWorld('jarvisDesktop', {
  getMode: () => ipcRenderer.invoke('mode:get'),
  requestMode: (mode) => ipcRenderer.send('mode:request', mode),
  onModeChanged: (callback) => {
    const listener = (_event, mode) => callback(mode)
    ipcRenderer.on('mode:changed', listener)
    return () => ipcRenderer.removeListener('mode:changed', listener)
  },
  setOverlayInteractive: (interactive) => ipcRenderer.send('overlay:set-interactive', interactive),
  moveOverlayTo: (x, y) => ipcRenderer.send('overlay:move-to', { x, y }),
})
