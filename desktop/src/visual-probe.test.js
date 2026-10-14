'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const probe = require('./visual-probe')
const { parseSmoke } = require('./main')

test('single CLI flag delegates fresh confined directory validation; conflicts fail before filesystem access', () => {
  const calls = []
  const parse = (args, root) => { calls.push({ args, root }); return { state:'inline-state' } }
  assert.equal(probe.parse([], parse, 'root'), null)
  assert.equal(probe.parse(['--visual-probe-state=inline-state'], parse, 'root'), 'inline-state')
  assert.deepEqual(calls, [{ args:['--smoke-entry=command-center', '--smoke-state=inline-state'], root:'root' }])
  for (const args of [ ['--visual-probe-unknown'], ['--visual-probe-state=a', '--visual-probe-state=b'],
    ...['smoke', 'overlay-probe', 'command-center-probe', 'window-state-probe', 'tray-probe'].map(p => ['--visual-probe-state=a', `--${p}-state=b`]) ]) assert.throws(() => probe.parse(args, parse, 'root'), /visual-probe-arguments/)
  assert.equal(calls.length, 1)
  for (const state of ['', '.', '../outside', path.resolve(__dirname), path.resolve(__dirname, '../../..')]) assert.throws(() => probe.parse([`--visual-probe-state=${state}`], parseSmoke, path.resolve(__dirname, '../..')), /smoke-/)
})

test('exact operation, host, size, sequential id and bounded request allowlists', () => {
  for (const operation of ['observe', 'quit']) assert.deepEqual(probe.validateRequest({ id:1, operation }), { id:1, operation })
  for (const host of probe.HOSTS) for (const size of Object.keys(probe.SIZES)) assert.equal(probe.validateRequest({ id:1, operation:'capture', host, size }).host, host)
  for (const value of [null, [], {}, { id:0, operation:'observe' }, { id:33, operation:'observe' }, { id:1.1, operation:'observe' },
    { id:1, operation:'eval' }, { id:1, operation:'observe', code:'evil' }, { id:1, operation:'capture', host:'../evil', size:'1920x1080' },
    { id:1, operation:'capture', host:'overlay', size:'__proto__' }, { id:1, operation:'capture', host:'overlay', size:'3840x2160' },
    { id:1, operation:'capture', host:'overlay', size:'1920x1080', reference:'evil.png' }, { id:1, operation:'capture', host:'overlay', size:'1920x1080', tolerance:255 }]) assert.throws(() => probe.validateRequest(value))
  assert.throws(() => probe.validateRequest({ id:3, operation:'observe' }, 1), /request-order/)
  assert.throws(() => probe.validateRequest({ id:1, operation:'observe' }, 2), /request-order/)
  assert.equal(probe.validateRequest({ id:32, operation:'quit' }, 31).id, 32)
  assert.equal(probe.validateRequest({ id:1, operation:'observe' }, 1).id, 1)
})

test('BGRA any-channel comparison: fixed tolerance inclusive boundary, count once, optional red mask', () => {
  assert.equal(probe.PER_CHANNEL_TOLERANCE, 51)
  assert.equal(probe.MAX_DIFF_RATIO, 0.01)
  const ref = Buffer.alloc(8)
  assert.deepEqual(probe.compare(ref, ref, 2, 1), { width:2, height:1, diffPixels:0, ratio:0 })
  for (const delta of [50,51,52]) for (let c = 0; c < 4; c++) {
    const cap = Buffer.from(ref); cap[c] = delta
    const result = probe.compare(ref, cap, 2, 1, { diffMask:true })
    assert.equal(result.diffPixels, delta > 51 ? 1 : 0)
    assert.equal(result.ratio, delta > 51 ? 0.5 : 0)
    assert.deepEqual(result.diffMask, delta > 51 ? Buffer.from([0,0,255,255,0,0,0,0]) : ref)
    assert.ok(ref.equals(Buffer.alloc(8)), 'pure comparison must not mutate input')
  }
  const cap = Buffer.from([255,255,255,255,0,0,0,0])
  assert.equal(probe.compare(ref, cap, 2, 1).diffPixels, 1)
})

test('size mismatch including equal-byte-count aspect mismatch is never silently cropped', () => {
  const bitmap = Buffer.alloc(8)
  for (const [ref,cap,w,h,opts] of [ [bitmap,Buffer.alloc(4),2,1,{}], [bitmap,bitmap,0,1,{}], [bitmap,bitmap,1.5,1,{}],
    [bitmap,bitmap,2,1,{referenceSize:{width:1,height:2}}], [bitmap,bitmap,2,1,{captureSize:{width:1,height:2}}], [bitmap,bitmap,2,1,{tolerance:-1}], [bitmap,bitmap,2,1,{tolerance:256}] ]) assert.throws(() => probe.compare(ref,cap,w,h,opts))
})

test('immutable inventory exactly covers tracked Phase 2 references', () => {
  const root = path.resolve(__dirname, '../..')
  const files = ['command-center', 'overlay'].flatMap(host => {
    const dir = `ui/src/testing/${host}.spec.ts-snapshots`
    return fs.readdirSync(path.join(root, dir)).filter(f => f.endsWith('.png')).map(f => `${dir}/${f}`)
  })
  assert.deepEqual(probe.REFERENCES.map(r => r.reference).sort(), files.sort())
  assert.equal(new Set(probe.REFERENCES.map(r => r.id)).size, 32)
  assert.equal(probe.REFERENCES.filter(r => !r.reason).length, 6)
})

// Fully inline filesystem, native images, windows and timer fixtures: contract
// tests never use ignored run directories or require a local Windows runtime.
function controller({ request = { id:1, operation:'observe' }, size = 32, symlink = false, renameError = null, smallDisplay = false, mismatch = false, geometry = {}, hung = false, referenceMissing = false, framed = false, readError = null, baseline = undefined } = {}) {
  const files = new Map([['inline/request.json', typeof request === 'string' ? request : JSON.stringify(request)]])
  const queue = [], quits = [], marks = [], calls = []
  const io = { existsSync:f => files.has(f), lstatSync:f => ({ size, isFile:() => true, isSymbolicLink:() => symlink && f.endsWith('request.json') }), readFileSync:f => { if (readError) { const code=readError; readError=null; throw Object.assign(new Error(code),{code}) } return files.get(f) }, writeFileSync:(f,d) => files.set(f,d), renameSync:(a,b) => { if (renameError) throw Object.assign(new Error(renameError), { code:renameError }); files.set(b,files.get(a)); files.delete(a) } }
  let bounds = { x:0,y:0,width:1920,height:1080 }
  const image = { getSize:() => ({ width:1920, height:1080 }), toPNG:() => Buffer.from('inline PNG'), toBitmap:() => Buffer.alloc(1920*1080*4), isEmpty:() => referenceMissing }
  const win = { isDestroyed:() => false, isVisible:() => true, setPosition:(x,y) => { bounds.x=x; bounds.y=y }, setContentSize:(w,h) => { bounds.width=w; bounds.height=h; calls.push('size') }, getContentBounds:() => ({ ...bounds, width:mismatch ? 1000 : bounds.width }), getBounds:() => ({ ...bounds, width:bounds.width + (framed ? 22 : 0), height:bounds.height + (framed ? 82 : 0) }),
    webContents:{ getZoomFactor:() => 1, executeJavaScript:async code => { assert.equal(code,probe.SETTLE); calls.push('fonts'); if (hung) return new Promise(() => {}); return { mounted:true, fonts:'loaded', reducedMotion:true, width:1920, height:1080, native:true, workspace:'Overview', expanded:request.host === 'overlay-expanded', ...geometry } }, capturePage:async () => { calls.push('capture'); return image } } }
  probe.create({ state:'inline', baseline, io, later:(fn,ms) => queue.push({ fn,ms }), quit:code => quits.push(code), getWindows:() => new Map([['command-center',win],['overlay',win]]), requestMode:mode => { calls.push(mode); return true }, displays:() => [...(framed ? [{ bounds:{ x:-1920,y:1173,width:1920,height:1080 } }] : []), { bounds:{ x:0,y:0,width:smallDisplay ? 1200 : 3840,height:2160 } }], nativeImage:{ createFromPath:f => { calls.push(f); return image }, createFromBitmap:(bitmap,opts) => { assert.equal(bitmap.length,opts.width*opts.height*4); return image } }, mark:(stage,fields) => marks.push({ stage,...fields }) })
  return { files, queue, quits, marks, calls, async tick(ms) { const i=queue.findIndex(t => t.ms === ms); assert.ok(i >= 0, `missing timer ${ms}`); queue.splice(i,1)[0].fn(); for (let j=0;j<20;j++) await Promise.resolve() } }
}

test('observe writes bounded response; repeated id is inert; quit ends controller', async () => {
  const c = controller(); await c.tick(100)
  const observation = JSON.parse(c.files.get('inline/response.json'))
  assert.equal(observation.id,1)
  assert.deepEqual(observation.windows['command-center'].contentBounds,{x:0,y:0,width:1920,height:1080})
  c.files.delete('inline/response.json'); await c.tick(100)
  assert.equal(c.files.has('inline/response.json'),false)
  c.files.set('inline/request.json',JSON.stringify({ id:2,operation:'quit' })); await c.tick(100)
  assert.deepEqual(c.quits,[0])
})

test('baseline CLI arg combos: state required, one valid baseline, no duplicates', () => {
  const parse = () => ({ state:'inline-state' })
  assert.equal(probe.parse(['--visual-probe-state=s', '--visual-probe-baseline=win32'], parse, 'r'), 'inline-state')
  assert.equal(probe.parse(['--visual-probe-baseline=win32', '--visual-probe-state=s'], parse, 'r'), 'inline-state')
  for (const args of [['--visual-probe-baseline=win32'], ['--visual-probe-state=s', '--visual-probe-baseline=linux'], ['--visual-probe-state=s', '--visual-probe-baseline='], ['--visual-probe-state=s', '--visual-probe-baseline=win32', '--visual-probe-baseline=win32'], ['--visual-probe-state=s', '--visual-probe-baseline=win32', '--visual-probe-x=1'], ['--visual-probe-state=s', '--visual-probe-baseline=win32', '--smoke-state=b']]) assert.throws(() => probe.parse(args, parse, 'r'), /visual-probe-arguments/)
  assert.equal(probe.parseBaseline(['--visual-probe-state=s']), null)
  assert.equal(probe.parseBaseline(['--visual-probe-state=s', '--visual-probe-baseline=win32']), 'win32')
})

test('capture waits for fonts and exact geometry before native capture; decodes references and writes PNG/mask', async () => {
  const c=controller({ request:{id:1,operation:'capture',host:'command-center',size:'1920x1080'} })
  await c.tick(100); assert.ok(!c.calls.includes('capture'))
  await c.tick(500)
  const row=JSON.parse(c.files.get('inline/response.json')).scenarios[0]
  assert.equal(row.status,'PASS'); assert.equal(row.ratio,0)
  assert.ok(c.calls.indexOf('fonts') < c.calls.indexOf('capture'))
  assert.ok(c.files.has('inline/capture-command-center-1920x1080.png'))
  assert.ok(c.files.has(`inline/diff-${row.id}.png`))
  assert.deepEqual(c.marks.map(m => m.stage),['visual-capture','visual-comparison'])
})

for (const [name,options,timer] of [ ['oversize',{size:1025},100], ['symlink',{symlink:true},100], ['malformed JSON',{request:'{'},100], ['permanent write failure',{renameError:'EIO'},100] ]) test(`controller rejects ${name}`,async () => {
  const c=controller(options); await c.tick(timer); assert.deepEqual(c.quits,[1]); assert.equal(c.marks.at(-1).stage,'visual-probe-error')
})

test('request rename ENOENT retries next poll; other read failures fail closed',async () => {
  const c=controller({readError:'ENOENT'}); await c.tick(100)
  assert.deepEqual(c.quits,[]); assert.equal(c.files.has('inline/response.json'),false)
  await c.tick(100); assert.equal(JSON.parse(c.files.get('inline/response.json')).id,1)
  const d=controller({readError:'EACCES'}); await d.tick(100)
  assert.deepEqual(d.quits,[1]); assert.equal(d.marks.at(-1).reason,'EACCES')
  for (const code of ['EPERM','EBUSY']) {
    const e=controller({readError:code}); await e.tick(100)
    assert.deepEqual(e.quits,[]); await e.tick(100); assert.equal(JSON.parse(e.files.get('inline/response.json')).id,1)
  }
})

test('response sharing violations retry only five times',async () => {
  const c=controller({renameError:'EPERM'}); await c.tick(100)
  for(let i=0;i<5;i++) await c.tick(50)
  assert.deepEqual(c.quits,[1])
})

for (const [name,options] of [['monitor too small',{smallDisplay:true}],['exact viewport mismatch',{mismatch:true}]]) test(`capture is BLOCKED for ${name}, not compared or cropped`,async () => {
  const c=controller({ request:{id:1,operation:'capture',host:'command-center',size:'1920x1080'},...options })
  await c.tick(100); if (!options.smallDisplay) await c.tick(500)
  const row=JSON.parse(c.files.get('inline/response.json')).scenarios[0]
  assert.equal(row.status,'BLOCKED'); assert.ok(row.reason); assert.equal(row.capture,null); assert.ok(!c.calls.includes('capture'))
})

test('framed 1080p content selects available 4K display instead of incorrectly blocking on 1080p display',async () => {
  const c=controller({ request:{id:1,operation:'capture',host:'command-center',size:'1920x1080'},framed:true })
  await c.tick(100); await c.tick(500)
  assert.equal(JSON.parse(c.files.get('inline/response.json')).scenarios[0].status,'PASS')
  const capture=c.marks.find(m => m.stage === 'visual-capture')
  assert.equal(capture.bounds.x,0)
  assert.equal(capture.bounds.width,1942)
  assert.equal(capture.bounds.height,1162)
})

test('undecodable reference is FAIL after capture, never PASS',async () => {
  const c=controller({ request:{id:1,operation:'capture',host:'command-center',size:'1920x1080'},referenceMissing:true })
  await c.tick(100); await c.tick(500)
  assert.equal(JSON.parse(c.files.get('inline/response.json')).scenarios[0].status,'FAIL')
})

test('font observer timeout and reduced-motion failure close nonzero; global deadline exists',async () => {
  const request={id:1,operation:'capture',host:'command-center',size:'1920x1080'}
  const c=controller({request,hung:true}); await c.tick(100); await c.tick(500); await c.tick(10000)
  assert.deepEqual(c.quits,[1]); assert.equal(c.marks.at(-1).reason,'capture-timeout')
  const d=controller({request,geometry:{reducedMotion:false}}); await d.tick(100); await d.tick(500)
  assert.deepEqual(d.quits,[1]); assert.ok(!d.calls.includes('capture'))
  const e=controller(); await e.tick(180000); assert.deepEqual(e.quits,[1])
})

test('preload retains exactly its existing public keys; no visual renderer API', () => {
  let api
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'preload.js'),'utf8'), { require:id => { assert.equal(id,'electron'); return {contextBridge:{exposeInMainWorld:(name,value) => {assert.equal(name,'jarvisDesktop');api=value}},ipcRenderer:{}} } })
  assert.deepEqual(Object.keys(api).sort(),['core','getMode','moveOverlayTo','onModeChanged','requestMode','setOverlayInteractive','settings'])
  assert.deepEqual(Object.keys(api.core).sort(),['control','getState','onEvent','onState'])
  assert.deepEqual(Object.keys(api.settings).sort(),['get','onChanged','onOpenRequested','setLaunchAtLogin'])
})

test('baseline mode compares against the win32 baseline path; missing baseline is FAIL baseline-missing-or-undecodable',async () => {
  const request={ id:1,operation:'capture',host:'overlay',size:'1920x1080' }
  const c=controller({ request,baseline:'win32' }); await c.tick(100); await c.tick(500)
  const row=JSON.parse(c.files.get('inline/response.json')).scenarios[0]
  assert.equal(row.reference,'desktop/visual-baselines/overlay-compact-idle-1920x1080-win32.png'); assert.equal(row.status,'PASS')
  assert.ok(c.calls.some(x => typeof x === 'string' && x.endsWith('overlay-compact-idle-1920x1080-win32.png')))
  const d=controller({ request,baseline:'win32',referenceMissing:true }); await d.tick(100); await d.tick(500)
  const bad=JSON.parse(d.files.get('inline/response.json')).scenarios[0]
  assert.equal(bad.status,'FAIL'); assert.equal(bad.reason,'baseline-missing-or-undecodable')
  const e=controller({ request,referenceMissing:true }); await e.tick(100); await e.tick(500)
  assert.equal(JSON.parse(e.files.get('inline/response.json')).scenarios[0].reason,'reference-missing-or-undecodable')
})
