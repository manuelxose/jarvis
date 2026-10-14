'use strict'

// Blesses a completed native capture run as the Windows baseline set. Reads only
// from the run directory, writes only desktop/visual-baselines, never follows symlinks.
const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')
const smoke = require('./native-smoke.cjs')
const { pngExists } = require('./native-visual.cjs')
const { REFERENCES, SIZES, baselinePath } = require('../src/visual-probe')
const ROOT = path.resolve(__dirname, '../..')
const within = (root, target) => { const rel = path.relative(root, target); return rel && rel !== '..' && !rel.startsWith(`..${path.sep}`) && !path.isAbsolute(rel) }
const regularFile = file => { const stat = fs.lstatSync(file); return stat.isFile() && !stat.isSymbolicLink() }

function bless({ from, root = ROOT, now = () => new Date().toISOString() }) {
  if (typeof from !== 'string' || !from) throw new Error('usage: visual-baselines.cjs --from <run-dir>')
  const runDir = path.resolve(root, from)
  if (!within(root, runDir)) throw new Error('run-outside-worktree')
  smoke.localOutput(root, path.relative(root, runDir))
  if (fs.lstatSync(runDir).isSymbolicLink() || !fs.lstatSync(runDir).isDirectory()) throw new Error('run-not-a-directory')
  const reportFile = path.join(runDir, 'report.json')
  if (!regularFile(reportFile) || fs.lstatSync(reportFile).size > 8 * 1024 * 1024) throw new Error('report-invalid')
  let report
  try { report = JSON.parse(fs.readFileSync(reportFile, 'utf8')) } catch { throw new Error('report-invalid') }
  if (!report || typeof report !== 'object') throw new Error('report-invalid')
  if (report.referenceSet === 'win32-baseline') throw new Error('run-is-baseline-comparison')
  const host = report.host
  if (['error', undefined].includes(report.status) || report.reason || !host || host.platform !== 'win32' || host.error || host.exitCode !== 0 ||
    !Array.isArray(host.remaining) || !Array.isArray(host.cleanupErrors) || host.remaining.length || host.cleanupErrors.length ||
    (report.recovery && (!Array.isArray(report.recovery.remaining) || !Array.isArray(report.recovery.cleanupErrors) || report.recovery.remaining.length || report.recovery.cleanupErrors.length)) || report.cleanupFailure) throw new Error('run-incomplete')
  const plan = REFERENCES.filter(r => !r.reason).map(r => {
    const source = path.join(runDir, 'state', `capture-${r.host}-${r.size}.png`)
    if (!pngExists(runDir, path.relative(root, source), r.size, 'capture', root)) throw new Error(`capture-invalid:${r.id}`)
    return { id:r.id, source, file:baselinePath(r.id), size:r.size }
  })
  const outDir = path.join(root, 'desktop', 'visual-baselines')
  if (fs.existsSync(outDir) && (fs.lstatSync(outDir).isSymbolicLink() || !fs.lstatSync(outDir).isDirectory())) throw new Error('baseline-dir-invalid')
  const files = plan.map(item => {
    const data = fs.readFileSync(item.source), target = path.join(root, item.file)
    if (fs.existsSync(target) && fs.lstatSync(target).isSymbolicLink()) throw new Error('baseline-symlink')
    return { ...item, target, data }
  })
  fs.mkdirSync(outDir, { recursive:true })
  const manifestFile = path.join(outDir, 'manifest.json')
  if (fs.existsSync(manifestFile) && fs.lstatSync(manifestFile).isSymbolicLink()) throw new Error('baseline-symlink')
  for (const f of files) fs.writeFileSync(f.target, f.data)
  let electron = null
  try { electron = JSON.parse(fs.readFileSync(path.join(root, 'desktop', 'node_modules', 'electron', 'package.json'), 'utf8')).version } catch { /* optional fact */ }
  const manifest = {
    sourceRun:path.relative(root, runDir).split(path.sep).join('/'), generatedAt:now(),
    host:{ electron, displays:(host.displays || []).map(d => ({ id:d.id, scaleFactor:d.scaleFactor, bounds:d.bounds })), scaleFactors:[...new Set((host.displays || []).map(d => d.scaleFactor))] },
    files:files.map(f => ({ id:f.id, file:f.file, sha256:crypto.createHash('sha256').update(f.data).digest('hex'), width:SIZES[f.size][0], height:SIZES[f.size][1] })),
  }
  fs.writeFileSync(manifestFile, JSON.stringify(manifest, null, 2) + '\n')
  return manifest
}
function main(argv = process.argv.slice(2)) {
  try {
    if (argv.length !== 2 || argv[0] !== '--from') throw new Error('usage: visual-baselines.cjs --from <run-dir>')
    console.log(JSON.stringify(bless({ from:argv[1] }))); return 0
  } catch (e) { console.error(JSON.stringify({ status:'FAIL', reason:e.message })); return 1 }
}
if (require.main === module) process.exitCode = main()
module.exports = { bless, main }
