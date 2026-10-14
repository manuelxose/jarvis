'use strict'

// Main-process / CLI only. Fixed observation code, never supplied renderer JS.
const fs = require('node:fs')
const path = require('node:path')
// Playwright defaults to a 0.2 perceptual YIQ threshold. Our required any-channel
// metric uses its normalized channel-space analogue (0.2 * 255 = 51), NOT YIQ.
// Frozen before native execution; rasterization differences remain real failures.
const PER_CHANNEL_TOLERANCE = 51
const MAX_DIFF_RATIO = 0.01
const SIZES = Object.freeze({ '1920x1080': Object.freeze([1920, 1080]), '2560x1440': Object.freeze([2560, 1440]) })
// overlay-expanded is a fixed native host/mode variant, not renderer state input.
const HOSTS = Object.freeze(['command-center', 'overlay', 'overlay-expanded'])
const BUDGET = 32
const ROOT = path.resolve(__dirname, '../..')
const REFERENCES = Object.freeze(['command-center', 'overlay'].flatMap(host => {
  const states = host === 'command-center' ? ['overview', 'conversation', 'memory', 'system', 'agents', 'development', 'media', 'settings'] : ['compact-idle', 'listening', 'speaking', 'processing', 'expanded', 'notification', 'error', 'disconnected']
  return states.flatMap(state => Object.keys(SIZES).map((size, i) => ({
    id: `${host}-${state}-${size}`, host:state === 'expanded' ? 'overlay-expanded' : host, size,
    reference: `ui/src/testing/${host}.spec.ts-snapshots/${host}-${state}-desktop-${i ? '1440p' : '1080p'}-linux.png`,
    reason: state === (host === 'command-center' ? 'overview' : 'compact-idle') || state === 'expanded' ? null :
      host === 'command-center' ? 'Non-Overview workspace is not selected by the CLI capture contract; renderer injection is forbidden.' :
      'Browser simulation state is not exposed by native mode controls; renderer injection or a live Core/state integration would be required.',
  })))
}).map(Object.freeze))
const BASELINE_SETS = Object.freeze(['win32'])
const baselinePath = id => `desktop/visual-baselines/${id}-win32.png`
const SETTLE = `(async () => {
  await document.fonts.ready;
  await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  return {mounted:Boolean(document.getElementById('root')?.childElementCount),
    fonts:document.fonts.status, reducedMotion:matchMedia('(prefers-reduced-motion: reduce)').matches,
    width:innerWidth, height:innerHeight, native:Boolean(window.jarvisDesktop),
    workspace:document.querySelector('button[aria-current="page"]')?.textContent?.trim(),
    expanded:[...document.querySelectorAll('button')].some(b => b.textContent.trim() === 'Collapse')};
})()`
function parse(argv, parseSmoke, root) {
  const args = argv.filter(a => a.startsWith('--visual-probe-'))
  if (!args.length) return null
  const states = args.filter(a => a.startsWith('--visual-probe-state=')), baselines = args.filter(a => a.startsWith('--visual-probe-baseline='))
  if (states.length !== 1 || baselines.length > 1 || states.length + baselines.length !== args.length || (baselines.length && !BASELINE_SETS.includes(baselines[0].slice('--visual-probe-baseline='.length))) || argv.some(a => /^--(?:smoke-|overlay-probe-|command-center-probe-|window-state-probe-|tray-probe-)/.test(a))) throw new Error('visual-probe-arguments')
  return parseSmoke(['--smoke-entry=command-center', `--smoke-state=${states[0].slice('--visual-probe-state='.length)}`], root).state
}
function parseBaseline(argv) {
  const value = argv.find(a => a.startsWith('--visual-probe-baseline='))
  return value ? value.slice('--visual-probe-baseline='.length) : null
}
function validateRequest(value, last = 0) {
  if (!value || Array.isArray(value) || !Number.isInteger(value.id) || value.id < 1 || value.id > BUDGET) throw new Error('request-invalid')
  const capture = value.operation === 'capture'
  if (Object.keys(value).sort().join(',') !== (capture ? 'host,id,operation,size' : 'id,operation') || !['capture', 'observe', 'quit'].includes(value.operation) || (capture && (!HOSTS.includes(value.host) || !Object.hasOwn(SIZES, value.size)))) throw new Error('operation-denied')
  if (value.id !== last && value.id !== last + 1) throw new Error('request-order')
  return value
}
// nativeImage.toBitmap() is BGRA. Any-channel deltas are permutation invariant;
// diff masks are also BGRA, ready for nativeImage.createFromBitmap().
function compare(refBitmap, capBitmap, width, height, opts = {}) {
  if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1 || width > 2560 || height > 1440 || !Buffer.isBuffer(refBitmap) || !Buffer.isBuffer(capBitmap) || refBitmap.length !== width * height * 4 || capBitmap.length !== width * height * 4 || (opts.referenceSize && (opts.referenceSize.width !== width || opts.referenceSize.height !== height)) || (opts.captureSize && (opts.captureSize.width !== width || opts.captureSize.height !== height))) throw new Error('bitmap-size-mismatch')
  const tolerance = opts.tolerance ?? PER_CHANNEL_TOLERANCE
  if (!Number.isInteger(tolerance) || tolerance < 0 || tolerance > 255) throw new Error('invalid-tolerance')
  const diffMask = opts.diffMask ? Buffer.alloc(width * height * 4) : null
  let diffPixels = 0
  for (let p = 0; p < refBitmap.length; p += 4) {
    let different = false
    for (let channel = 0; channel < 4; channel++) if (Math.abs(refBitmap[p + channel] - capBitmap[p + channel]) > tolerance) different = true
    if (different) { diffPixels++; if (diffMask) { diffMask[p + 2] = 255; diffMask[p + 3] = 255 } }
  }
  return { width, height, diffPixels, ratio: diffPixels / (width * height), ...(diffMask ? { diffMask } : {}) }
}
function create({ state, baseline = null, later, quit, getWindows, requestMode, displays, nativeImage, mark, io = fs }) {
  let last = 0, stopped = false
  const request = path.join(state, 'request.json'), response = path.join(state, 'response.json')
  const stop = code => { if (!stopped) { stopped = true; quit(code) } }
  const wait = ms => new Promise(resolve => later(resolve, ms))
  const bounded = promise => Promise.race([promise, new Promise((_, reject) => later(() => reject(new Error('capture-timeout')), 10000))])
  function write(file, data) {
    if (io.existsSync(file) && io.lstatSync(file).isSymbolicLink()) throw new Error('artifact-symlink')
    io.writeFileSync(file, data)
  }
  async function capture(value) {
    const { host, size } = value, [width, height] = SIZES[size]
    const refs = REFERENCES.filter(r => r.host === host && r.size === size && !r.reason)
    const referenceOf = r => baseline ? baselinePath(r.id) : r.reference
    const rows = refs.map(r => ({ id:r.id, reference:referenceOf(r), capture:null, status:'BLOCKED', ratio:null, reason:'capture-not-run' }))
    const windowHost = host.startsWith('overlay') ? 'overlay' : host
    const mode = host === 'overlay-expanded' ? 'OVERLAY_EXPANDED' : host === 'overlay' ? 'OVERLAY_COMPACT' : 'COMMAND_CENTER_WINDOWED'
    if (host === 'overlay-expanded' && !requestMode('OVERLAY_COMPACT')) throw new Error('mode-denied')
    if (!requestMode(mode)) throw new Error('mode-denied')
    let win
    for (let attempt = 0; attempt < 100; attempt++) {
      win = getWindows().get(windowHost)
      if (win && !win.isDestroyed() && win.isVisible()) break
      await wait(50)
    }
    if (!win || win.isDestroyed() || !win.isVisible()) throw new Error('window-not-ready')
    // A framed Command Center needs room for non-client borders as well as
    // its content. Otherwise a 1080p monitor is selected over an available 4K
    // monitor and the exact 1080p content is incorrectly reported BLOCKED.
    const outer = win.getBounds(), inner = win.getContentBounds()
    const frameWidth = Math.max(0, outer.width - inner.width)
    const frameHeight = Math.max(0, outer.height - inner.height)
    const display = displays().find(d => d.bounds.width >= width + frameWidth && d.bounds.height >= height + frameHeight)
    if (!display) { rows.forEach(r => { r.reason = 'Attached monitors cannot contain the reference viewport at scale factor 1.' }); return rows }
    win.setPosition(display.bounds.x, display.bounds.y)
    win.setContentSize(width, height)
    await wait(500)
    const geometry = await bounded(win.webContents.executeJavaScript(SETTLE))
    const content = win.getContentBounds(), bounds = win.getBounds()
    const contained = displays().some(d => bounds.x >= d.bounds.x && bounds.y >= d.bounds.y && bounds.x + bounds.width <= d.bounds.x + d.bounds.width && bounds.y + bounds.height <= d.bounds.y + d.bounds.height)
    if (!contained || content.width !== width || content.height !== height || geometry.width !== width || geometry.height !== height) {
      rows.forEach(r => { r.reason = 'Native window cannot reach and fit the exact reference content size on attached monitors.' }); return rows
    }
    if (!geometry.mounted || !geometry.native || geometry.fonts !== 'loaded' || !geometry.reducedMotion || win.webContents.getZoomFactor() !== 1 || (windowHost === 'command-center' ? geometry.workspace !== 'Overview' : geometry.expanded !== (host === 'overlay-expanded'))) throw new Error('renderer-not-settled')
    const image = await bounded(win.webContents.capturePage())
    if (stopped) throw new Error('controller-stopped')
    const capSize = image.getSize(), file = path.join(state, `capture-${host}-${size}.png`)
    write(file, image.toPNG())
    mark('visual-capture', { host, size, artifact:path.relative(ROOT, file), content, bounds, geometry })
    for (const row of rows) {
      row.capture = path.relative(ROOT, file)
      try {
        const ref = nativeImage.createFromPath(path.join(ROOT, row.reference))
        if (ref.isEmpty()) throw new Error(baseline ? 'baseline-missing-or-undecodable' : 'reference-missing-or-undecodable')
        const result = compare(ref.toBitmap(), image.toBitmap(), width, height, { referenceSize:ref.getSize(), captureSize:capSize, diffMask:true })
        const diff = path.join(state, `diff-${row.id}.png`)
        write(diff, nativeImage.createFromBitmap(result.diffMask, { width, height, scaleFactor:1 }).toPNG())
        Object.assign(row, { status:result.ratio <= MAX_DIFF_RATIO ? 'PASS' : 'FAIL', ratio:result.ratio, diffPixels:result.diffPixels, diff:path.relative(ROOT, diff), reason:result.ratio <= MAX_DIFF_RATIO ? null : 'Pixel difference exceeds the fixed 0.01 maximum ratio.' })
      } catch (e) { row.status = 'FAIL'; row.reason = e.message }
      mark('visual-comparison', row)
    }
    return rows
  }
  async function poll() {
    if (stopped) return
    try {
      let value
      try {
        if (io.existsSync(request)) {
          const stat = io.lstatSync(request)
          if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 1024) throw new Error('request-budget')
          value = validateRequest(JSON.parse(io.readFileSync(request, 'utf8')), last)
        }
      } catch (e) {
        // Move-Item on a WSL share can briefly remove or lock the prior file
        // (ENOENT, or EPERM/EBUSY sharing violations); the 180s controller timeout
        // bounds retries. Every other error fails closed.
        if (!['ENOENT', 'EPERM', 'EBUSY'].includes(e.code)) throw e
      }
      if (value) {
        if (value.id !== last) {
          const scenarios = value.operation === 'capture' ? await capture(value) : []
          if (stopped) return
          const monitors = displays()
          if (!Array.isArray(monitors) || monitors.length > 32) throw new Error('display-budget')
          const windows = {}
          for (const [host, win] of getWindows()) {
            if (!['overlay', 'command-center'].includes(host) || win.isDestroyed()) throw new Error('window-lost')
            windows[host] = { visible:win.isVisible(), bounds:win.getBounds(), contentBounds:win.getContentBounds(), zoom:win.webContents.getZoomFactor() }
          }
          const result = { id:value.id, pid:process.pid, displays:monitors, windows, scenarios }
          const tmp = response + '.tmp'
          write(tmp, JSON.stringify(result))
          if (io.existsSync(response) && io.lstatSync(response).isSymbolicLink()) throw new Error('artifact-symlink')
          for (let attempt = 0; ; attempt++) {
            try { io.renameSync(tmp, response); break }
            catch (e) { if (!['EPERM', 'EACCES', 'EBUSY'].includes(e.code) || attempt >= 5) throw e; await wait(50) }
          }
          last = value.id
          if (value.operation === 'quit') { stop(0); return }
        }
      }
    } catch (e) { mark('visual-probe-error', { reason:e.message }); stop(1); return }
    later(poll, 100)
  }
  later(() => { mark('visual-probe-error', { reason:'controller-timeout' }); stop(1) }, 180000)
  later(poll, 100)
}
module.exports = { parse, parseBaseline, baselinePath, BASELINE_SETS, validateRequest, compare, create, REFERENCES, HOSTS, SIZES, BUDGET, PER_CHANNEL_TOLERANCE, MAX_DIFF_RATIO, SETTLE }
