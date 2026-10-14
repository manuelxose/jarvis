'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const fs = require('node:fs')
const { spawnSync } = require('node:child_process')
const h = require('./native-command-center.cjs')
const probe = require('../src/command-center-probe')
const config = {exe:'C:\\runtime\\electron.exe',app:'C:\\work\\desktop',root:'C:\\work',directory:'C:\\work\\desktop-native-command-center-test'}
const target = {pid:123,started:'1234567890',path:config.exe}
const rect = {x:0,y:0,width:1100,height:760}
function input(kind='mouse') { return {kind,target,revalidated:{...target},hwnd:'42',foreground:'42',visible:true,hitValidated:true,clientBounds:rect,client:{x:40,y:40},screen:{x:80,y:80},flags:[2,4]} }
function report() {
  const phases = [['initial','resized'],['initial','maximized','restored'],['fullscreen','button-exit','escape-exit'],['compact','expanded','compact-again'],['roundtrip-1','roundtrip-2','roundtrip-3']]
  const fixture = {platform:'win32',realInput:true,preflight:{ctypes:true},cleanup:{remaining:[],errors:[]},scenarios:h.REQUIRED.map((id,i) => ({id,status:'PASS',target:{...target},hwnd:'42',inputs:i<2?[]:[input(i===2?'escape':'mouse')],screenshots:['screenshot-01.bmp'],observations:phases[i].map(phase => ({phase,target:{...target},centerHwnd:'42',overlayHwnd:'43',mounted:true,nativeAgreement:true,rect:{...rect},displays:[{scaleFactor:1,workArea:rect}],nativeDisplay:{dpiX:144,workArea:rect,monitor:rect},mode:i===3?'OVERLAY_COMPACT':'COMMAND_CENTER_WINDOWED',centerVisible:i!==3,centerFullscreen:false,overlayVisible:true,overlayHit:true,windowCount:2}))}))}
  for (const s of fixture.scenarios) for (const o of s.observations) {
    o.mode = o.phase.startsWith('fullscreen') ? 'COMMAND_CENTER_FULLSCREEN' : o.phase.startsWith('expanded') ? 'OVERLAY_EXPANDED' : o.phase.startsWith('compact') ? 'OVERLAY_COMPACT' : 'COMMAND_CENTER_WINDOWED'
    o.centerFullscreen = o.mode === 'COMMAND_CENTER_FULLSCREEN'
    o.maximized = o.phase === 'maximized'
    if (o.phase === 'resized') o.rect.width = 1000
  }
  return fixture
}
test('import is inert; complete independent native evidence passes', () => {
  assert.equal(h.validate(report(),()=>true),'PASS')
  assert.equal(probe.parse([],()=>{throw Error('called')}),null)
})
test('every required scenario and phase is necessary', () => {
  for (let i=0;i<5;i++) {
    const r=report(); r.scenarios.splice(i,1); assert.throws(()=>h.validate(r,()=>true),/incomplete/)
    const p=report(); p.scenarios[i].observations.pop(); assert.throws(()=>h.validate(p,()=>true),/phases/)
  }
  const r=report(); r.scenarios[1].id=r.scenarios[0].id; assert.throws(()=>h.validate(r),/incomplete/)
})
test('malformed native observations, identity, geometry and screenshots fail closed', () => {
  const mutate = [r=>r.platform='linux',r=>r.realInput=false,r=>r.preflight.ctypes=false,
    r=>r.scenarios[0].target.pid=0,r=>r.scenarios[0].hwnd='0',r=>r.scenarios[0].observations[0].target.started='different',
    r=>r.scenarios[0].observations[0].rect.width=NaN,r=>r.scenarios[0].observations[0].mounted=false,
    r=>r.scenarios[0].observations[0].nativeAgreement=false,r=>r.scenarios[0].observations[0].nativeDisplay.dpiX=NaN,
    r=>r.scenarios[0].observations[0].displays=[],r=>r.scenarios[0].screenshots=[]]
  for (const fn of mutate) {const r=report();fn(r);assert.throws(()=>h.validate(r,()=>true))}
  assert.throws(()=>h.validate(report(),()=>false),/missing-native/)
})
test('foreign focus, PID reuse, off-target input, absent Escape and hidden center capture are rejected', () => {
  const mutate = [r=>r.scenarios[2].inputs[0].foreground='99',r=>r.scenarios[2].inputs[0].revalidated.started='9',
    r=>r.scenarios[2].inputs[0].client.x=1100,r=>r.scenarios[2].inputs[0].kind='eval',r=>r.scenarios[2].inputs[0].kind='mouse',
    r=>r.scenarios[3].observations[0].centerVisible=true,r=>r.scenarios[3].observations[0].centerFullscreen=true,
    r=>r.scenarios[3].observations[0].overlayHit=false,r=>r.scenarios[4].observations[0].windowCount=3,
    r=>r.scenarios[4].observations[1].overlayHwnd='44']
  for (const fn of mutate) {const r=report();fn(r);assert.throws(()=>h.validate(r,()=>true))}
})
test('BLOCKED retains precise gaps; third-party flag cannot waive native required evidence or FAIL', () => {
  for (const status of ['BLOCKED','FAIL']) {
    const r=report();r.scenarios[1]={id:h.REQUIRED[1],status,reason:'foreground-denied'}
    assert.equal(h.validate(r,()=>true),status)
    const e={status,native:r};assert.equal(h.acceptGaps(e,['explorer','terminal']),e)
    delete r.scenarios[1].reason;assert.throws(()=>h.validate(r,()=>true),/gap-reason/)
  }
  for(const gaps of [['browser'],['command-center'],['explorer','explorer'],[''],['explorer','vscode','terminal','explorer']]) assert.throws(()=>h.acceptGaps({},gaps),/invalid-accepted/)
})
test('cleanup is mandatory and recovery runs even after bridge death or malformed reports', () => {
  for (const mutation of [r=>r.cleanup.remaining.push(target),r=>r.cleanup.errors.push('denied'),r=>delete r.cleanup]) {const r=report();mutation(r);assert.throws(()=>h.validate(r,()=>true),/cleanup/)}
  for (const failure of ['timeout','malformed','survivors','exit','recovery']) {
    let calls=0
    const e=h.run({config,directory:path.resolve('desktop-native-command-center-test'),exists:()=>true,readReport:()=>failure==='malformed'?{}:report(),bridge:()=>{
      calls++
      if(calls===1) {if(failure==='timeout')throw Error('bridge-timeout');return {error:null,electronExit:failure==='exit'?null:0,pythonExit:0,remaining:failure==='survivors'?[target]:[],cleanupErrors:[]}}
      return {remaining:[],cleanupErrors:failure==='recovery'?['denied']:[]}
    }})
    assert.equal(calls,2);assert.equal(e.status,'FAIL')
  }
})
test('phase/mode, resize, maximize, fullscreen geometry and complete input cannot be forged', () => {
  for (const fn of [r=>r.scenarios[0].observations[1].rect={...rect}, r=>r.scenarios[1].observations[1].maximized=false,
    r=>r.scenarios[2].observations[0].mode='COMMAND_CENTER_WINDOWED',r=>r.scenarios[2].observations[0].rect.width=1000,
    r=>r.scenarios[3].observations[1].mode='OVERLAY_COMPACT',r=>r.scenarios[3].inputs[0].flags=[2],r=>r.scenarios[3].inputs[0].hwnd='99']) {
    const r=report();fn(r);assert.throws(()=>h.validate(r,()=>true))
  }
})
test('native identity must correlate with the host-owned root, not just a self-consistent report', () => {
  for (const owned of [[target],[{...target,started:'2'}],[],[target,target],[{...target,path:'foreign'}]]) {
    let calls=0
    const e=h.run({config,directory:path.resolve('desktop-native-command-center-test'),exists:()=>true,readReport:report,bridge:()=>++calls===1?{electronExit:0,pythonExit:0,remaining:[],cleanupErrors:[],owned}:{remaining:[],cleanupErrors:[]}})
    assert.equal(e.status,owned.length===1 && owned[0]===target?'PASS':'FAIL')
  }
})
test('bridge retains UTC identity, owned lineage, quoting and bounded monitor; no renderer injection', () => {
  const s=h.bridgeScript(config), recovery=h.recoveryScript(config)
  assert.match(s,/command-center-probe-state=/);assert.match(s,/windows-command-center-probe\.py/)
  assert.doesNotMatch(s,/--overlay-probe-state=|\.StartTime\.Ticks|--no-sandbox|taskkill/i)
  assert.match(s,/StartTime\.ToUniversalTime\(\)\.Ticks/);assert.match(s,/AddSeconds\(105\)/)
  assert.match(recovery,/Same-Process/);assert.match(recovery,/probe-owned\.json/)
  assert.throws(()=>h.bridgeScript({...config,root:'bad\npath'}),/invalid-path/)
})
test('CLI parse rejects duplicates, mixed controllers and unsafe delegated state', () => {
  let args
  assert.equal(probe.parse(['--command-center-probe-state=/owned/state'],value=>{args=value;return {state:'/owned/state'}},'/owned'),'/owned/state')
  assert.deepEqual(args,['--smoke-entry=command-center','--smoke-state=/owned/state'])
  for(const argv of [['--command-center-probe-eval=x'],['--command-center-probe-state=x','--command-center-probe-state=y'],['--command-center-probe-state=x','--smoke-state=x'],['--command-center-probe-state=x','--overlay-probe-state=x']]) assert.throws(()=>probe.parse(argv,()=>({state:'x'})),/arguments/)
  assert.throws(()=>probe.parse(['--command-center-probe-state=../outside'],()=>{throw Error('state-escape')}),/state-escape/)
})
function controller(value, overrides={}) {
  const scheduled=[],quits=[],writes=[];let mode='COMMAND_CENTER_WINDOWED'
  const io={existsSync:()=>value!==null,lstatSync:()=>({isSymbolicLink:()=>false}),statSync:()=>({size:20}),readFileSync:()=>typeof value==='string'?value:JSON.stringify(value),writeFileSync:(p,s)=>writes.push(JSON.parse(s)),renameSync:()=>{},...overrides}
  const win={isDestroyed:()=>false,getNativeWindowHandle:()=>Buffer.from([42,0,0,0]),isVisible:()=>true,isFullScreen:()=>false,isMaximized:()=>false,getBounds:()=>rect,getContentBounds:()=>rect,webContents:{getZoomFactor:()=>1,executeJavaScript:async()=>({mounted:true})}}
  probe.create({state:'/test/state',later:(fn,ms)=>scheduled.push({fn,ms}),quit:code=>quits.push(code??0),getWindows:()=>new Map([['command-center',win]]),getMode:()=>mode,requestMode:m=>{mode=m;return true},displays:()=>[],io})
  return {scheduled,quits,writes,setMode:m=>{mode=m}}
}
test('main-only controller rejects malformed requests, reordering, arbitrary operations, symlinks and excessive payload', async () => {
  for(const value of ['{bad', {id:0,operation:'observe'}, {id:121,operation:'observe'}, {id:2,operation:'observe'}, {id:1,operation:'eval'}, {id:1,operation:'windowed'}, {id:1,operation:'observe',js:'bad'}]) {
    const c=controller(value);await c.scheduled.find(s=>s.ms===100).fn();assert.deepEqual(c.quits,[1]);assert.equal(c.writes.length,0)
  }
  for(const overrides of [{lstatSync:()=>({isSymbolicLink:()=>true})},{statSync:()=>({size:1025})},{renameSync:()=>{throw Error('write-failed')}}]) {const c=controller({id:1,operation:'observe'},overrides);await c.scheduled.find(s=>s.ms===100).fn();assert.deepEqual(c.quits,[1])}
})
test('controller read-only observation, quit and hard deadline are bounded', async () => {
  const c=controller({id:1,operation:'observe'});await c.scheduled.find(s=>s.ms===100).fn();assert.equal(c.writes[0].windows['command-center'].hwnd,'42');assert.deepEqual(c.quits,[])
  const q=controller({id:1,operation:'quit'});await q.scheduled.find(s=>s.ms===100).fn();assert.deepEqual(q.quits,[0])
  const absent=controller(null);absent.scheduled.find(s=>s.ms===120000).fn();assert.deepEqual(absent.quits,[1])
  assert.doesNotMatch(probe.GEOMETRY,/require\(|fetch\(|requestMode\(|ipcRenderer|eval\(/)
})
test('Win32 probe imports inertly and rejects identity replacement / budgets without native launch', () => {
  const code=`import importlib.util, pathlib, time
s=importlib.util.spec_from_file_location('cc',pathlib.Path('desktop/scripts/windows-command-center-probe.py'))
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
p=object.__new__(m.Probe);p.started=time.monotonic()-90
try:p.budget();raise AssertionError('budget accepted')
except m.Blocked:pass
p.directory=pathlib.Path('unused');p.target={'pid':1,'started':'1','path':'electron'};p.center=42;p.overlay=None
m.bounded_json=lambda _: [{'pid':1,'started':'2','path':'electron'}]
try:p.identify({'pid':1,'windows':{'command-center':{'hwnd':'42'}}});raise AssertionError('identity accepted')
except m.Blocked as e:assert str(e)=='main-process-identity-changed'
m.bounded_json=lambda _: [{'pid':99,'started':'1','path':'electron'}]
try:p.identify({'pid':1});raise AssertionError('unowned accepted')
except m.Blocked as e:assert str(e)=='unowned-main-process'
print('inert import, deadline, PID reuse, unowned process: PASS')`
  const result=spawnSync('python3',['-c',code],{encoding:'utf8',cwd:path.resolve(__dirname,'../..'),timeout:5000})
  assert.equal(result.status,0,result.stderr)
  const source=fs.readFileSync(path.join(__dirname,'windows-command-center-probe.py'),'utf8')
  assert.match(source,/Keyboard\(27,0,flags,0,0\)/);assert.match(source,/finally:/)
})
