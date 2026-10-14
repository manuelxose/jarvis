'use strict'

// Privileged acceptance seam: CLI/main only, fixed read-only DOM expression and
// bounded observe -> hide -> show -> quit sequence. Never exposed in preload.
const fs = require('node:fs')
const path = require('node:path')
const GEOMETRY = `(() => {
  const rect = el => { if (!el || !el.getClientRects().length) return null;
    const r = el.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height}; };
  const buttons = [...document.querySelectorAll('[data-overlay-interactive] button')];
  return {card:rect(document.querySelector('[data-overlay-interactive]')),
    drag:rect(document.querySelector('[data-overlay-drag]')),
    toggle:rect(buttons.find(b => b.textContent.trim() === 'Expand' || b.textContent.trim() === 'Collapse')),
    label:buttons.find(b => b.textContent.trim() === 'Expand' || b.textContent.trim() === 'Collapse')?.textContent.trim()};
})()`

function parseProbe(argv, parseSmoke, root) {
  const args = argv.filter(arg => arg.startsWith('--overlay-probe-'))
  if (!args.length) return null
  if (args.length !== 1 || !args[0].startsWith('--overlay-probe-state=') || argv.some(arg => arg.startsWith('--smoke-'))) throw new Error('overlay-probe-arguments')
  return parseSmoke(['--smoke-entry=overlay', `--smoke-state=${args[0].slice('--overlay-probe-state='.length)}`], root).state
}

function createProbe({ state, later, quit, getWindow, getMode, requestMode, io = fs }) {
  let last = 0, phase = 0, cycles = 0
  const request = path.join(state, 'request.json'), response = path.join(state, 'response.json')
  async function poll() {
    try {
      if (io.existsSync(request)) {
        if (io.lstatSync(request).isSymbolicLink() || io.statSync(request).size > 1024) throw new Error('probe-request-budget')
        const value = JSON.parse(io.readFileSync(request, 'utf8'))
        if (!Number.isInteger(value.id) || value.id < 1 || value.id > 200 || Object.keys(value).sort().join(',') !== 'id,operation') throw new Error('probe-request-invalid')
        if (value.id !== last) {
          if (value.id !== last + 1) throw new Error('probe-request-order')
          const { operation } = value
          if (operation === 'observe' && phase !== 3) { /* bounded read only */ }
          else if (operation === 'hide' && phase === 0 && cycles < 4) { phase = 1; if (!requestMode('HIDDEN')) throw new Error('probe-hide-denied') }
          else if (operation === 'show' && phase === 1) { phase = 0; cycles++; if (!requestMode('OVERLAY_COMPACT')) throw new Error('probe-show-denied') }
          else if (operation === 'quit' && phase === 0) { phase = 3 }
          else throw new Error('probe-operation-denied')
          const win = getWindow()
          if (!win || win.isDestroyed()) throw new Error('probe-window-lost')
          const geometry = await win.webContents.executeJavaScript(GEOMETRY)
          const handle = win.getNativeWindowHandle()
          const reply = { id: value.id, pid: process.pid, hwnd: String(handle.length === 8 ? handle.readBigUInt64LE() : handle.readUInt32LE()), mode: getMode(), visible: win.isVisible(), bounds: win.getBounds(), zoom: win.webContents.getZoomFactor(), geometry }
          const temporary = response + '.tmp'
          io.writeFileSync(temporary, JSON.stringify(reply))
          // Windows readers can briefly deny replacement on the WSL UNC share.
          // Keep the previous reply intact and bound retries; all other errors fail closed.
          for (let attempt = 0; ; attempt++) {
            try { io.renameSync(temporary, response); break }
            catch (error) {
              if (!['EPERM', 'EACCES', 'EBUSY'].includes(error.code) || attempt >= 5) throw error
              await new Promise(resolve => later(resolve, 50))
            }
          }
          last = value.id
          if (phase === 3) { quit(); return }
        }
      }
    } catch { quit(1); return }
    later(poll, 100)
  }
  later(() => quit(1), 120000)
  later(poll, 100)
}
module.exports = { parseProbe, createProbe, GEOMETRY }
