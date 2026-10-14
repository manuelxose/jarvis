'use strict'

// CLI-only, bounded controller. Never accepts code, arbitrary channels or Core commands.
const fs = require('node:fs')
const path = require('node:path')
function parse(argv, parseSmoke, root) {
  const args = argv.filter(a => a.startsWith('--tray-probe-'))
  if (!args.length) return null
  const state = args.find(a => a.startsWith('--tray-probe-state='))
  if (!state || args.length !== (args.includes('--tray-probe-login-item') ? 2 : 1) ||
      args.some(a => a !== state && a !== '--tray-probe-login-item') ||
      argv.some(a => /^--(?:smoke|overlay-probe|command-center-probe|window-state-probe)-/.test(a))) throw new Error('tray-probe-arguments')
  return { state: parseSmoke(['--smoke-entry=overlay', `--smoke-state=${state.slice('--tray-probe-state='.length)}`], root).state,
    loginItem: args.includes('--tray-probe-login-item') }
}
function create({ state, loginItem, later, quit, setLaunchAtLogin, mark, io = fs }) {
  let last = 0
  const request = path.join(state, 'tray-request.json')
  function poll() {
    try {
      if (io.existsSync(request)) {
        if (io.lstatSync(request).isSymbolicLink() || io.statSync(request).size > 1024) throw new Error('request-budget')
        const r = JSON.parse(io.readFileSync(request, 'utf8'))
        if (!Number.isInteger(r.id) || r.id < 1 || r.id > 8 || Object.keys(r).sort().join(',') !== 'id,operation') throw new Error('request-invalid')
        if (r.id !== last) {
          if (r.id !== last + 1) throw new Error('request-order')
          if (r.operation === 'exit-desktop') { mark('tray-probe-command', { id: r.id, operation: r.operation }); quit(); return }
          if (!loginItem || !['login-enable', 'login-disable'].includes(r.operation)) throw new Error('operation-denied')
          const actual = setLaunchAtLogin(r.operation === 'login-enable')
          last = r.id
          mark('tray-probe-command', { id: r.id, operation: r.operation, openAtLogin: actual })
        }
      }
    } catch { mark('tray-probe-failed', { reason: 'controller' }); quit(1); return }
    later(poll, 100)
  }
  later(() => { mark('tray-probe-failed', { reason: 'deadline' }); quit(1) }, 120000)
  later(poll, 100)
}
module.exports = { parse, create }
