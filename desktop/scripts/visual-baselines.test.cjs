'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const crypto = require('node:crypto')
const { bless, main } = require('./visual-baselines.cjs')
const { REFERENCES, SIZES, baselinePath } = require('../src/visual-probe')
const available = REFERENCES.filter(r => !r.reason)
function png(size) {
  const b = Buffer.alloc(40); Buffer.from([137,80,78,71,13,10,26,10]).copy(b); b.write('IHDR', 12, 'ascii')
  b.writeUInt32BE(SIZES[size][0], 16); b.writeUInt32BE(SIZES[size][1], 20); b.write(size, 30, 'ascii'); return b
}
const goodHost = () => ({ platform:'win32', exitCode:0, error:null, remaining:[], cleanupErrors:[], displays:[{ id:1, scaleFactor:1, bounds:{ x:0, y:0, width:3840, height:2160 } }] })
function fixture(report = {}, { skip } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'vb-')), run = path.join(root, 'run'), state = path.join(run, 'state')
  fs.mkdirSync(state, { recursive:true })
  for (const r of available) if (r !== skip) fs.writeFileSync(path.join(state, `capture-${r.host}-${r.size}.png`), png(r.size))
  fs.writeFileSync(path.join(run, 'report.json'), JSON.stringify({ status:'FAIL', host:goodHost(), ...report }))
  return { root, run }
}
const fails = (f, re) => assert.throws(() => bless({ from:f.run, root:f.root }), re)

test('valid run writes 6 baselines and a manifest with correct sha256', () => {
  const f = fixture(), manifest = bless({ from:f.run, root:f.root, now:() => 'T' })
  assert.equal(manifest.files.length, 6); assert.equal(manifest.generatedAt, 'T'); assert.equal(manifest.sourceRun, 'run')
  for (const entry of manifest.files) {
    const ref = available.find(r => r.id === entry.id), data = fs.readFileSync(path.join(f.root, baselinePath(entry.id)))
    assert.equal(entry.file, baselinePath(entry.id)); assert.equal(entry.sha256, crypto.createHash('sha256').update(data).digest('hex'))
    assert.deepEqual([entry.width, entry.height], SIZES[ref.size]); assert.ok(data.equals(png(ref.size)))
  }
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(f.root, 'desktop/visual-baselines/manifest.json'), 'utf8')).host.scaleFactors, [1])
})
test('overlay compact and expanded map to their own captures', () => {
  const f = fixture(); bless({ from:f.run, root:f.root })
  assert.ok(fs.existsSync(path.join(f.root, baselinePath('overlay-compact-idle-1920x1080'))))
  assert.ok(fs.existsSync(path.join(f.root, baselinePath('overlay-expanded-2560x1440'))))
})
for (const [name, report] of [['error status', { status:'error' }], ['missing status', { status:undefined }], ['failed host exit', { host:{ ...goodHost(), exitCode:1 } }], ['surviving process', { host:{ ...goodHost(), remaining:[3] } }], ['cleanup errors', { host:{ ...goodHost(), cleanupErrors:['x'] } }], ['host error', { host:{ ...goodHost(), error:'boom' } }], ['wrong platform', { host:{ ...goodHost(), platform:'linux' } }], ['recovery dirty', { recovery:{ remaining:[1], cleanupErrors:[] } }], ['reason set', { reason:'native-exit-or-cleanup-failed' }], ['win32-baseline comparison run', { referenceSet:'win32-baseline' }]]) test(`rejects ${name}`, () => {
  const f = fixture(report); fails(f, /run-incomplete|run-is-baseline-comparison/)
  assert.ok(!fs.existsSync(path.join(f.root, 'desktop/visual-baselines')))
})
test('rejects wrong PNG size, missing capture and symlinked capture without writing', () => {
  const wrong = fixture(); fs.writeFileSync(path.join(wrong.run, 'state/capture-command-center-1920x1080.png'), png('2560x1440')); fails(wrong, /capture-invalid/)
  const missing = fixture({}, { skip:available[0] }); fails(missing, /capture-invalid/)
  const link = fixture(), file = path.join(link.run, 'state/capture-overlay-1920x1080.png'), real = path.join(link.root, 'real.png')
  fs.writeFileSync(real, png('1920x1080')); fs.rmSync(file); fs.symlinkSync(real, file); fails(link, /capture-invalid/)
  for (const f of [wrong, missing, link]) assert.ok(!fs.existsSync(path.join(f.root, 'desktop/visual-baselines')))
})
test('rejects out-of-tree run, symlinked run dir, malformed report and symlinked output', () => {
  const f = fixture(); assert.throws(() => bless({ from:os.tmpdir(), root:f.root }), /run-outside-worktree/)
  assert.throws(() => bless({ from:'../x', root:f.root }), /run-outside-worktree/)
  assert.throws(() => bless({ from:'', root:f.root }), /usage/)
  const alias = path.join(f.root, 'alias'); fs.symlinkSync(f.run, alias); assert.throws(() => bless({ from:alias, root:f.root }))
  fs.writeFileSync(path.join(f.run, 'report.json'), '{'); fails(f, /report-invalid/)
  const g = fixture(), out = path.join(g.root, 'desktop'), outside = fs.mkdtempSync(path.join(os.tmpdir(), 'vb-out-'))
  fs.mkdirSync(out); fs.symlinkSync(outside, path.join(out, 'visual-baselines')); fails(g, /baseline-dir-invalid/)
  assert.deepEqual(fs.readdirSync(outside), [])
  const h = fixture(); fs.mkdirSync(path.join(h.root, 'desktop/visual-baselines'), { recursive:true })
  fs.symlinkSync(path.join(h.root, 'run/report.json'), path.join(h.root, baselinePath(available[0].id))); fails(h, /baseline-symlink/)
})
test('CLI requires exactly --from <dir>', () => {
  const errors = console.error; console.error = () => {}
  try { assert.equal(main([]), 1); assert.equal(main(['--from']), 1); assert.equal(main(['--to', 'x']), 1) } finally { console.error = errors }
})
