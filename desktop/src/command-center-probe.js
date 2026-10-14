'use strict'

// CLI/main-only bounded acceptance controller. No renderer API or supplied JS.
const fs = require('node:fs')
const path = require('node:path')
const { GEOMETRY: OVERLAY } = require('./overlay-probe')
const GEOMETRY = `(() => {
  const rect = el => { if (!el?.getClientRects().length) return null;
    const r = el.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height}; };
  const button = label => rect([...document.querySelectorAll('button')].find(b => b.getAttribute('aria-label') === label));
  return {mounted:Boolean(document.getElementById('root')?.childElementCount),
    enter:button('Enter fullscreen'), exit:button('Exit fullscreen'),
    overlay:button('Return to compact overlay'), viewport:{width:innerWidth,height:innerHeight},
    overflow:document.documentElement.scrollWidth > innerWidth};
})()`
function parse(argv, parseSmoke, root) {
  const args = argv.filter(a => a.startsWith('--command-center-probe-'))
  if (!args.length) return null
  if (args.length !== 1 || !args[0].startsWith('--command-center-probe-state=') || argv.some(a => a.startsWith('--smoke-') || a.startsWith('--overlay-probe-'))) throw new Error('command-center-probe-arguments')
  return parseSmoke(['--smoke-entry=command-center', `--smoke-state=${args[0].slice('--command-center-probe-state='.length)}`], root).state
}
function create({ state, later, quit, getWindows, getMode, requestMode, displays, io = fs }) {
  let last = 0, cycles = 0
  const request = path.join(state, 'request.json'), response = path.join(state, 'response.json')
  async function poll() {
    try {
      if (io.existsSync(request)) {
        if (io.lstatSync(request).isSymbolicLink() || io.statSync(request).size > 1024) throw new Error('request-budget')
        const value = JSON.parse(io.readFileSync(request, 'utf8'))
        if (!Number.isInteger(value.id) || value.id < 1 || value.id > 120 || Object.keys(value).sort().join(',') !== 'id,operation') throw new Error('request-invalid')
        if (value.id !== last) {
          if (value.id !== last + 1) throw new Error('request-order')
          if (value.operation === 'windowed' && getMode().startsWith('OVERLAY_') && cycles < 4) {
            cycles++; if (!requestMode('COMMAND_CENTER_WINDOWED')) throw new Error('windowed-denied')
          } else if (!['observe', 'quit'].includes(value.operation)) throw new Error('operation-denied')
          const windows = {}
          for (const [host, win] of getWindows()) {
            if (win.isDestroyed()) throw new Error('window-lost')
            const handle = win.getNativeWindowHandle()
            windows[host] = { hwnd:String(handle.length === 8 ? handle.readBigUInt64LE() : handle.readUInt32LE()),
              visible:win.isVisible(), fullscreen:win.isFullScreen(), maximized:win.isMaximized(),
              bounds:win.getBounds(), contentBounds:win.getContentBounds(), zoom:win.webContents.getZoomFactor(),
              geometry:await win.webContents.executeJavaScript(host === 'overlay' ? OVERLAY : GEOMETRY) }
          }
          const tmp = response + '.tmp'
          io.writeFileSync(tmp, JSON.stringify({ id:value.id, pid:process.pid, mode:getMode(), displays:displays(), windows }))
          for (let attempt = 0; ; attempt++) {
            try { io.renameSync(tmp, response); break }
            catch (e) { if (!['EPERM', 'EACCES', 'EBUSY'].includes(e.code) || attempt >= 5) throw e; await new Promise(resolve => later(resolve, 50)) }
          }
          last = value.id
          if (value.operation === 'quit') { quit(); return }
        }
      }
    } catch { quit(1); return }
    later(poll, 100)
  }
  later(() => quit(1), 120000)
  later(poll, 100)
}
module.exports = { parse, create, GEOMETRY }
