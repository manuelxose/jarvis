'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const { aggregate, bridgeScript, run, pngExists } = require('./native-visual.cjs')
const { REFERENCES, SIZES, MAX_DIFF_RATIO, PER_CHANNEL_TOLERANCE, baselinePath } = require('../src/visual-probe')
const available = REFERENCES.filter(r => !r.reason)
function rows() { return available.map(ref => ({ id:ref.id, reference:ref.reference, capture:`inline/capture-${ref.host}-${ref.size}.png`, diff:`inline/diff-${ref.id}.png`, status:'PASS', ratio:0, diffPixels:0 })) }
const at = (report, id=available[0].id) => report.scenarios.find(r => r.id === id)

test('complete aggregation preserves all 32 references and blocks 26 unavailable states', () => {
  const result=aggregate(rows(),()=>true)
  assert.equal(result.status,'BLOCKED')
  assert.equal(result.scenarios.length,32)
  assert.equal(result.scenarios.filter(r => r.status === 'PASS').length,6)
  assert.equal(result.scenarios.filter(r => r.status === 'BLOCKED').length,26)
  assert.ok(result.scenarios.filter(r => r.status !== 'PASS').every(r => r.reason))
})

test('missing capture, diff, duplicate, result or wrong reference can never PASS', () => {
  for (const change of [r => r.shift(), r => r.push({...r[0]}), r => {r[0].capture=null}, r => {r[0].diff=null}, r => {r[0].reference='wrong.png'}]) {
    const values=rows();change(values);const result=aggregate(values,()=>true)
    assert.equal(result.status,'FAIL'); assert.equal(at(result).status,'FAIL'); assert.ok(at(result).reason)
  }
  assert.equal(at(aggregate(rows())).status,'FAIL')
})

test('fixed ratio boundary is inclusive; excessive, missing, nonfinite, negative or uncorrelated metrics FAIL', () => {
  const values=rows(), pixels=SIZES[available[0].size][0]*SIZES[available[0].size][1]
  values[0].ratio=MAX_DIFF_RATIO;values[0].diffPixels=pixels*MAX_DIFF_RATIO
  assert.equal(at(aggregate(values,()=>true)).status,'PASS')
  for (const value of [0.010001,null,undefined,NaN,Infinity,-1]) {
    values[0].ratio=value
    assert.equal(at(aggregate(values,()=>true)).status,'FAIL')
  }
  values[0].ratio=0;values[0].diffPixels=1
  assert.equal(at(aggregate(values,()=>true)).status,'FAIL')
})

test('FAIL is never demoted; BLOCKED is never promoted, including unavailable reference spoofing', () => {
  const values=rows();values[0].status='FAIL';values[0].ratio=0;values[0].reason='real comparison failure'
  assert.equal(at(aggregate(values,()=>true)).status,'FAIL')
  values[0].status='BLOCKED';values[0].reason='monitor-too-small'
  assert.equal(at(aggregate(values,()=>true)).status,'BLOCKED')
  const unavailable=REFERENCES.find(r=>r.reason)
  values.push({...unavailable,status:'PASS',ratio:0,capture:'anything',diff:'anything',diffPixels:0})
  assert.equal(at(aggregate(values,()=>true),unavailable.id).status,'BLOCKED')
})

test('unknown status or scenario and malformed collection force FAIL', () => {
  const values=rows();values[0].status='MAYBE'
  assert.equal(aggregate(values,()=>true).status,'FAIL')
  assert.equal(at(aggregate(values,()=>true)).status,'FAIL')
  assert.equal(aggregate([...rows(),{id:'unknown',status:'PASS'}],()=>true).status,'FAIL')
  assert.equal(aggregate(null).status,'FAIL')
  assert.equal(aggregate([...rows(),null],()=>true).status,'FAIL')
})

test('Windows paths are normalized and image validation is fail-closed and confined', () => {
  const values=rows();values[0].capture='inline\\capture-test.png';values[0].diff='inline\\diff-test.png'
  assert.equal(at(aggregate(values,()=>true)).capture,'inline/capture-test.png')
  const root=path.resolve(__dirname,'../..')
  for(const artifact of ['../outside.png','desktop/src/visual-probe.js','missing.png']) assert.equal(pngExists(path.join(root,'inline'),artifact,'1920x1080','capture'),false)
})

const config={exe:'C:\\runtime\\electron.exe',app:'C:\\active worktree\\desktop',directory:'C:\\active worktree\\desktop-native-visual-inline'}
const cleanup={remaining:[],cleanupErrors:[]}
function host() { return {platform:'win32',rootPid:17,owned:[{pid:17,started:'123',path:config.exe}],exitCode:0,error:null,remaining:[],cleanupErrors:[],scenarios:rows()} }

test('bridge launches CLI in active worktree, bounded JSON and requests, no renderer input or global kill', () => {
  const source=bridgeScript(config)
  assert.match(source,/--visual-probe-state=/)
  assert.match(source,/-WorkingDirectory 'C:\\active worktree\\desktop'/)
  assert.match(source,/\.Length -gt 65536/)
  assert.match(source,/\.AddSeconds\(25\)/)
  assert.match(source,/\$reply.pid -ne \$rootPid/)
  assert.match(source,/ToUniversalTime\(\)\.Ticks/)
  assert.match(source,/Same-Process \$record \$live/)
  assert.match(source,/Stop-Process -InputObject \$live/)
  assert.match(source,/Set-Content -LiteralPath \$tmp -Value \$json -Encoding Ascii/)
  assert.doesNotMatch(source,/New-Object|\[IO.File\]::WriteAllText|\.WaitForExit\(/)
  assert.match(source,/Wait-Process -Id \$rootPid -Timeout 5/)
  assert.match(source,/\$attempt -ge 5/)
  assert.match(source,/Start-Sleep -Milliseconds 100/)
  const requests = JSON.parse(source.match(/ConvertFrom-Json -InputObject '(\[.*\])'/)[1])
  assert.ok(JSON.stringify(requests).split('').every(char => char.charCodeAt(0) < 128))
  assert.doesNotMatch(source,/Invoke-Expression|taskkill|Stop-Process -Name|executeJavaScript|--smoke-entry/)
  assert.equal((source.match(/"operation":"capture"/g)||[]).length,6)
  assert.throws(()=>bridgeScript({...config,app:'evil\npath'}),/invalid-path/)
})

test('run preserves BLOCKED aggregate and always checks owned cleanup', () => {
  const calls=[]
  const report=run({config,directory:path.resolve(__dirname,'inline'),exists:()=>true,invoke:script=>{calls.push(script);return calls.length===1?host():cleanup}})
  assert.equal(report.status,'BLOCKED');assert.equal(calls.length,2)
  assert.match(calls[1],/ownership-journal-unavailable/)
})

for(const [name,change] of [['bad exit',h=>{h.exitCode=1}],['wrong platform',h=>{h.platform='linux'}],['unowned pid',h=>{h.rootPid=99}],['surviving child',h=>{h.remaining=[17]}],['cleanup error',h=>{h.cleanupErrors=['error']}],['malformed report',()=>null]]) test(`run rejects ${name} and still attempts recovery`,()=>{
  let calls=0
  const report=run({config,directory:__dirname,exists:()=>true,invoke:()=>{calls++;if(calls===2)return cleanup;const h=host();return change(h)===null?null:h}})
  assert.equal(report.status,'FAIL');assert.equal(calls,2);assert.ok(report.reason)
})

test('bridge loss and malformed JSON still recover; uncertain recovery forces FAIL', () => {
  for(const failure of ['bridge-ETIMEDOUT','invalid-json']) {
    let calls=0
    const report=run({config,directory:__dirname,invoke:()=>{calls++;if(calls===1)throw new Error(failure);return cleanup}})
    assert.equal(report.status,'FAIL');assert.equal(calls,2);assert.equal(report.reason,failure)
    assert.equal(report.scenarios.length,32)
  }
  let calls=0
  const report=run({config,directory:__dirname,exists:()=>true,invoke:()=>++calls===1?host():{remaining:[17],cleanupErrors:[]}})
  assert.equal(report.status,'FAIL');assert.equal(report.cleanupFailure,'recovery-cleanup-failed')
})

test('baseline mode: bridge carries the arg; aggregate checks win32 paths; constants frozen', () => {
  assert.doesNotMatch(bridgeScript(config),/visual-probe-baseline/)
  assert.match(bridgeScript({...config,baseline:'win32'}),/--visual-probe-baseline=win32/)
  const win=rows().map(r => ({...r,reference:baselinePath(r.id)}))
  const ok=aggregate(win,()=>true,'win32')
  assert.equal(ok.status,'BLOCKED'); assert.equal(at(ok).status,'PASS'); assert.equal(at(ok).reference,baselinePath(available[0].id))
  assert.equal(at(aggregate(rows(),()=>true,'win32')).status,'FAIL')
  assert.equal(at(aggregate(win,()=>true)).status,'FAIL')
  const blocked=REFERENCES.find(r => r.reason)
  assert.equal(at(aggregate(win,()=>true,'win32'),blocked.id).reference,blocked.reference)
  assert.equal(PER_CHANNEL_TOLERANCE,51); assert.equal(MAX_DIFF_RATIO,0.01)
})

test('run records referenceSet and baseline hashes; default set is phase2-linux',() => {
  const cleanup={remaining:[],cleanupErrors:[]}
  const base=run({config:{...config,baseline:'win32'},directory:__dirname,exists:()=>true,hashes:()=>[{id:'x',file:'f',sha256:'abc'}],invoke:()=>cleanup})
  assert.equal(base.referenceSet,'win32-baseline'); assert.deepEqual(base.baselines,[{id:'x',file:'f',sha256:'abc'}])
  const plain=run({config,directory:__dirname,exists:()=>true,invoke:()=>cleanup})
  assert.equal(plain.referenceSet,'phase2-linux'); assert.equal(plain.baselines,undefined)
})
