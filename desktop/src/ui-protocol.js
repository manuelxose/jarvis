'use strict'

const fs = require('node:fs')
const path = require('node:path')
const ENTRIES = Object.freeze({ 'command-center': 'index.html', overlay: 'overlay.html' })
const CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' ws://127.0.0.1:* http://127.0.0.1:*; object-src 'none'; base-uri 'none'; frame-src 'none'"
const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.json': 'application/json', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.ico': 'image/x-icon', '.woff': 'font/woff', '.woff2': 'font/woff2', '.ttf': 'font/ttf' }

function parseRoute(raw) {
  // Inspect the raw path before URL's dot-segment normalization can erase it.
  if (typeof raw !== 'string' || raw.length > 4096 || /[\\\s]/.test(raw)) throw new Error('invalid-url')
  const match = /^app:\/\/(command-center|overlay)(\/[^?#]*)?(?:\?[^#]*)?(?:#.*)?$/.exec(raw)
  if (!match) throw new Error('invalid-origin')
  const rawPath = match[2] || '/'
  if (/%(?:2e|2f|5c|25)/i.test(rawPath)) throw new Error('encoded-path')
  let pathname
  try { pathname = decodeURIComponent(rawPath) } catch { throw new Error('invalid-encoding') }
  if (/[\x00-\x1f\x7f\\:*<>"|]/.test(pathname) || pathname.split('/').some(p => p === '.' || p === '..' || (p && /[. ]$/.test(p)))) throw new Error('traversal')
  const file = pathname === '/' ? ENTRIES[match[1]] : pathname.slice(1)
  if (file !== ENTRIES[match[1]] && file !== 'favicon.svg' && !(file.startsWith('assets/') && MIME[path.extname(file)] && !file.endsWith('.html'))) throw new Error('invalid-asset')
  return { host: match[1], file }
}

function contained(root, file) {
  const rel = path.relative(root, file)
  return rel !== '' && !rel.startsWith(`..${path.sep}`) && rel !== '..' && !path.isAbsolute(rel)
}

function createUiProtocol(dist, io = fs) {
  function read(file) {
    const root = io.realpathSync(dist)
    const target = path.resolve(dist, file)
    if (!contained(path.resolve(dist), target) || !contained(root, io.realpathSync(target))) throw Object.assign(new Error('escape'), { code: 'ESCAPE' })
    if (!io.statSync(target).isFile()) throw Object.assign(new Error('not-file'), { code: 'ENOENT' })
    return io.readFileSync(target)
  }
  function respond(request) {
    let route
    try { route = parseRoute(request.url) } catch { return new Response('forbidden', { status: 403 }) }
    try {
      return new Response(read(route.file), { headers: { 'Content-Type': MIME[path.extname(route.file)], 'Content-Security-Policy': CSP, 'X-Content-Type-Options': 'nosniff' } })
    } catch (err) {
      const status = err.code === 'ESCAPE' ? 403 : err.code === 'ENOENT' || err.code === 'ENOTDIR' ? 404 : 500
      return new Response(status === 403 ? 'forbidden' : status === 404 ? 'not found' : 'unreadable asset', { status })
    }
  }
  function preflight() {
    for (const [host, file] of Object.entries(ENTRIES)) {
      const html = read(file).toString('utf8')
      if (!/id=["']root["']/.test(html)) throw new Error('dist-incomplete')
      const refs = [...html.matchAll(/(?:src|href)=["'](\/[^"']+)["']/g)].map(m => m[1])
      if (!refs.some(ref => ref.endsWith('.js'))) throw new Error('dist-incomplete')
      for (const ref of refs) read(parseRoute(`app://${host}${ref}`).file)
    }
  }
  return { respond, preflight }
}

function allowedNavigation(url, host) {
  try { const route = parseRoute(url); return route.host === host && route.file === ENTRIES[host] } catch { return false }
}

module.exports = { createUiProtocol, parseRoute, allowedNavigation, ENTRIES, CSP }
