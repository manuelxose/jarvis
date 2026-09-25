// Minimal Electron shell PoC — proves window lifecycle + loading the Vite
// build, nothing more. Not the production shell; Tauri PoC is BLOCKED in
// this environment (no Rust/cargo). See docs/architecture/desktop-shell-adr.md.
const { app, BrowserWindow } = require('electron')
const path = require('node:path')

app.whenReady().then(() => {
  const win = new BrowserWindow({
    width: 900,
    height: 600,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    show: false,
  })

  win.once('ready-to-show', () => {
    console.log('LIFECYCLE: ready-to-show')
    win.show()
    console.log('LIFECYCLE: shown, frame=false transparent=true alwaysOnTop=' + win.isAlwaysOnTop())
    setTimeout(() => {
      console.log('LIFECYCLE: closing')
      win.close()
    }, 500)
  })

  win.on('closed', () => {
    console.log('LIFECYCLE: closed')
    app.quit()
  })

  win.loadFile(path.join(__dirname, '..', 'ui', 'dist', 'index.html'))
    .then(() => console.log('LIFECYCLE: loadFile resolved'))
    .catch((err) => {
      console.error('LIFECYCLE: loadFile failed', err)
      app.exit(1)
    })
})

app.on('window-all-closed', () => {
  console.log('LIFECYCLE: window-all-closed, quitting')
  app.quit()
})
