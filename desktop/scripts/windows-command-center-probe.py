"""Real Windows Command Center acceptance. Import is inert; stdlib only.
Uses the existing identity/focus/hit/cleanup-safe Win32 observer, not UI mocks.
"""
import argparse
import ctypes as C
from ctypes import wintypes as W
import importlib.util
import json
import math
from pathlib import Path
import sys
import time

_spec = importlib.util.spec_from_file_location('overlay_native', Path(__file__).with_name('windows-overlay-probe.py'))
_native = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_native)
Win32, Blocked, poll, atomic_json, bounded_json = (_native.Win32, _native.Blocked, _native.poll, _native.atomic_json, _native.bounded_json)
REQUIRED = ('windowed', 'maximize-restore', 'fullscreen-enter-exit', 'return-to-overlay', 'repeated-roundtrips')


class Probe:
    def __init__(self, root, directory):
        self.root, self.directory = root.resolve(), directory.resolve()
        if self.directory.parent != self.root or not self.directory.name.startswith('desktop-native-command-center-') or self.directory.is_symlink():
            raise Blocked('run-path-escape')
        self.started, self.sequence, self.photos = time.monotonic(), 0, 0
        self.native = Win32()
        self.target, self.center, self.overlay = None, None, None
        atomic_json(self.directory / 'recipients.json', [])  # no third-party launches
        self.native.u.ShowWindow.argtypes = [W.HWND, C.c_int]
        self.native.u.ShowWindow.restype = W.BOOL
        self.native.u.IsZoomed.argtypes = [W.HWND]
        self.native.u.IsZoomed.restype = W.BOOL
        self.native.u.MonitorFromWindow.argtypes = [W.HWND, W.DWORD]
        self.native.u.MonitorFromWindow.restype = W.HANDLE
        class Monitor(C.Structure):
            _fields_ = [('size', W.DWORD), ('monitor', W.RECT), ('work', W.RECT), ('flags', W.DWORD)]
        self.Monitor = Monitor
        self.native.u.GetMonitorInfoW.argtypes = [W.HANDLE, C.POINTER(Monitor)]
        self.native.u.GetMonitorInfoW.restype = W.BOOL
        self.shcore = C.WinDLL('shcore', use_last_error=True)
        self.shcore.GetDpiForMonitor.argtypes = [W.HANDLE, C.c_int, C.POINTER(W.UINT), C.POINTER(W.UINT)]
        self.shcore.GetDpiForMonitor.restype = C.c_long

    def budget(self):
        if time.monotonic() - self.started > 85:
            raise Blocked('session-budget')

    def request(self, operation='observe'):
        if operation != 'quit':
            self.budget()
        self.sequence += 1
        if self.sequence > 120:
            raise Blocked('request-budget')
        state = self.directory / 'state'
        poll(lambda: state.is_dir(), 8)
        atomic_json(state / 'request.json', {'id': self.sequence, 'operation': operation})
        def response():
            try:
                f = state / 'response.json'
                value = bounded_json(f) if f.exists() else None
                return value if value and value.get('id') == self.sequence else None
            except PermissionError:
                return None
        timeout = 3 if operation == 'quit' else min(15 if self.sequence == 1 else 3, max(0, self.started + 85 - time.monotonic()))
        return poll(response, timeout)

    def identify(self, reply):
        records = bounded_json(self.directory / 'owned.json')
        records = records if isinstance(records, list) else [records]
        if not records or len(records) > 512:
            raise Blocked('ownership-journal-invalid')
        record = next((r for r in records if r['pid'] == reply['pid']), None)
        if not record:
            raise Blocked('unowned-main-process')
        target = {k: record[k] for k in ('pid', 'started', 'path')}
        if self.target is not None and target != self.target:
            raise Blocked('main-process-identity-changed')
        self.target = target
        center = int(reply['windows']['command-center']['hwnd'])
        overlay = int(reply['windows']['overlay']['hwnd']) if 'overlay' in reply['windows'] else None
        if self.center and center != self.center or self.overlay and overlay != self.overlay:
            raise Blocked('window-singleton-changed')
        self.center, self.overlay = center, overlay
        self.native.window(center, target, visible=False)
        if overlay:
            self.native.window(overlay, target, visible=False)
        self.native.overlay_pid = target['pid']

    def display(self):
        monitor = self.native.u.MonitorFromWindow(self.center, 2)
        info = self.Monitor(); info.size = C.sizeof(info)
        self.native.checked(self.native.u.GetMonitorInfoW(monitor, C.byref(info)), 'monitor-info')
        x, y = W.UINT(), W.UINT()
        self.native.checked(self.shcore.GetDpiForMonitor(monitor, 0, C.byref(x), C.byref(y)) == 0, 'monitor-dpi')
        def rect(r):
            return {'x':r.left, 'y':r.top, 'width':r.right-r.left, 'height':r.bottom-r.top}
        return {'monitor':rect(info.monitor), 'workArea':rect(info.work), 'dpiX':x.value, 'dpiY':y.value, 'scale':x.value / 96, 'probeDeviceScaleFactor':1}

    def measure(self, c):
        n = self.native
        physical = n.rect(self.center)
        client, origin = n.rect(self.center, True), n.point(self.center, 0, 0)
        content, viewport = c['contentBounds'], c['geometry']['viewport']
        # Framed Win32 client includes a caption/menu strip above CSS content.
        dx, dy = content['x'] - origin['x'], content['y'] - origin['y']
        agreement = (all(abs(physical[k]-c['bounds'][k]) <= 2 for k in physical)
                     and dx >= 0 and dy >= 0 and dx + content['width'] <= client['width'] + 2
                     and dy + content['height'] <= client['height'] + 2
                     and abs(viewport['width'] - content['width']) <= 2
                     and abs(viewport['height'] - content['height']) <= 2)
        return physical, client, origin, content, agreement

    def observe(self, phase, mode, scenario, maximized=None):
        last = None
        measurement = None
        disagreement = None
        def ready():
            nonlocal last, measurement, disagreement
            reply = self.request()
            self.identify(reply)
            c = reply['windows']['command-center']; g = c['geometry']
            if reply['mode'] != mode or not g.get('mounted'):
                return None
            overlay = mode.startswith('OVERLAY_')
            if not overlay and (not c['visible'] or c['fullscreen'] != (mode == 'COMMAND_CENTER_FULLSCREEN')):
                return None
            if maximized is not None and c['maximized'] != maximized:
                return None
            if not overlay and not g.get('exit' if c['fullscreen'] else 'enter'):
                return None
            if overlay:
                o = reply['windows'].get('overlay')
                if c['visible'] or c['fullscreen'] or not o or not o['visible'] or not o['geometry'].get('toggle'):
                    return None
                if o['geometry'].get('label') != ('Expand' if mode == 'OVERLAY_COMPACT' else 'Collapse'):
                    return None
            measurement = self.measure(c)
            if not measurement[-1]:
                disagreement = {'physical':measurement[0], 'client':measurement[1], 'origin':measurement[2], 'content':measurement[3], 'viewport':g['viewport']}
                last = None
                return None
            fingerprint = (measurement[:4], mode, c['fullscreen'], c['maximized'])
            stable = last == fingerprint
            last = fingerprint
            return reply if stable else None
        try:
            reply = poll(ready, 5)
        except Blocked as e:
            if disagreement:
                raise Blocked('native-content-geometry-disagrees:' + json.dumps(disagreement,separators=(',',':'))) from e
            raise
        n, c = self.native, reply['windows']['command-center']
        n.window(self.center, self.target, visible=not mode.startswith('OVERLAY_'))
        physical, client, origin, content, agreement = measurement
        if maximized is not None and bool(n.u.IsZoomed(self.center)) != maximized:
            raise Blocked('native-maximize-disagrees')
        display = self.display()
        if c['fullscreen']:
            m = display['monitor']
            if any(abs(physical[k]-m[k]) > 2 for k in m):
                raise Blocked('fullscreen-not-monitor-sized')
        hit = False
        if mode.startswith('OVERLAY_'):
            o = reply['windows']['overlay']; b = o['geometry']['toggle']
            client_point = {'x':round(b['x']+b['width']/2), 'y':round(b['y']+b['height']/2)}
            screen = n.point(self.overlay, **client_point)
            n.window(self.overlay, self.target)
            n.checked(n.u.SetCursorPos(screen['x'],screen['y']), 'cursor-position')
            poll(lambda: n.hit(screen) == self.overlay, 2)
            hit = True
        observed = {'phase':phase, 'target':n.process(self.target['pid']), 'centerHwnd':str(self.center), 'overlayHwnd':str(self.overlay) if self.overlay else None,
                    'mode':mode, 'mounted':c['geometry']['mounted'], 'nativeAgreement':agreement,
                    'rect':physical, 'client':client, 'clientOrigin':origin, 'contentBounds':content, 'displays':reply['displays'], 'nativeDisplay':display,
                    'centerVisible':bool(n.u.IsWindowVisible(self.center)), 'centerFullscreen':c['fullscreen'], 'maximized':c['maximized'],
                    'overlayVisible':bool(n.u.IsWindowVisible(self.overlay)) if self.overlay else False, 'overlayHit':hit, 'windowCount':len(reply['windows']), 'geometry':c['geometry']}
        if len(scenario['observations']) >= 32:
            raise Blocked('observation-budget')
        scenario['observations'].append(observed)
        return reply

    def picture(self, scenario):
        self.native.focus(self.center,self.target)
        b = self.native.rect(self.center,True); p = self.native.point(self.center,0,0)
        b.update(p); b['width'] = min(b['width'],1600); b['height'] = min(b['height'],1000)
        self.photos += 1
        if self.photos > 32:
            raise Blocked('screenshot-budget')
        file = self.directory / ('screenshot-%02d.bmp' % self.photos)
        self.native.screenshot(file,b)
        scenario['screenshots'].append(file.relative_to(self.root).as_posix())

    def click(self, host, label, scenario):
        reply = self.request(); self.identify(reply)
        win = reply['windows'][host]; box = win['geometry'].get(label)
        if not box or not all(type(box.get(k)) in (float,int) and math.isfinite(box[k]) for k in ('x','y','width','height')) or box['width'] <= 0 or box['height'] <= 0:
            raise Blocked('control-not-visible')
        hwnd = int(win['hwnd']); self.native.focus(hwnd,self.target)
        content = win['contentBounds']
        # CSS content -> physical screen -> independently measured Win32 client.
        client = self.native.point(hwnd, round(content['x'] + box['x'] + box['width']/2),
                                   round(content['y'] + box['y'] + box['height']/2), to_screen=False)
        # Only move within the validated owned client. Never click a foreign hit.
        bounds = self.native.rect(hwnd,True)
        if not (0 <= client['x'] < bounds['width'] and 0 <= client['y'] < bounds['height']):
            raise Blocked('off-target-control')
        screen = self.native.point(hwnd,**client)
        self.native.checked(self.native.u.SetCursorPos(screen['x'],screen['y']), 'cursor-position')
        poll(lambda: self.native.hit(screen) == hwnd, 2)
        evidence = self.native.input(hwnd,self.target,client,[hwnd],flags=(),move=False)
        held = False
        try:
            for flag in (2,4):
                self.native.input(hwnd,self.target,client,[hwnd],flags=(flag,),move=False)
                held = flag == 2
        finally:
            if held:
                # Releasing a held button is safe even if focus was stolen;
                # never issue another down event to recover from failure.
                packet = self.native.Input(); packet.type = 0
                packet.data.mi = self.native.Mouse(0,0,0,4,0,0)
                self.native.u.SendInput(1,C.byref(packet),C.sizeof(packet))
        evidence.update(kind='mouse',flags=[2,4]); scenario['inputs'].append(evidence)

    def escape(self, scenario):
        n = self.native; n.focus(self.center,self.target)
        b = n.rect(self.center,True)
        evidence = n.input(self.center,self.target,{'x':b['width']//2,'y':b['height']//2},[self.center],flags=())
        class Keyboard(C.Structure):
            _fields_ = [('vk', W.WORD), ('scan', W.WORD), ('flags', W.DWORD), ('time',W.DWORD), ('extra',C.c_size_t)]
        held = False
        try:
            for flags in (0,2):
                n.window(self.center,self.target)
                if int(n.u.GetForegroundWindow() or 0) != self.center:
                    raise Blocked('escape-focus-changed')
                packet = n.Input(); packet.type = 1
                key = Keyboard(27,0,flags,0,0)
                C.memmove(C.byref(packet.data),C.byref(key),C.sizeof(key))
                n.checked(n.u.SendInput(1,C.byref(packet),C.sizeof(packet)) == 1,'escape-input-denied')
                held = flags == 0
        finally:
            if held:
                packet = n.Input(); packet.type = 1; key = Keyboard(27,0,2,0,0)
                C.memmove(C.byref(packet.data),C.byref(key),C.sizeof(key))
                n.u.SendInput(1,C.byref(packet),C.sizeof(packet))
        evidence['kind'] = 'escape'; scenario['inputs'].append(evidence)

    def run(self, scenario):
        id = scenario['id']; n = self.native
        self.observe('initial','COMMAND_CENTER_WINDOWED',scenario)
        self.picture(scenario)
        if id == 'windowed':
            n.window(self.center,self.target)
            before = n.rect(self.center)
            n.checked(n.u.SetWindowPos(self.center,None,0,0,1100,760,0x16),'native-resize')
            self.observe('resized','COMMAND_CENTER_WINDOWED',scenario)
            after = n.rect(self.center)
            if before['width'] == after['width'] and before['height'] == after['height']:
                raise Blocked('native-resize-unobserved')
        elif id == 'maximize-restore':
            before = n.rect(self.center)
            n.window(self.center,self.target); n.u.ShowWindow(self.center,3)
            self.observe('maximized','COMMAND_CENTER_WINDOWED',scenario,True)
            self.picture(scenario)
            n.window(self.center,self.target); n.u.ShowWindow(self.center,9)
            self.observe('restored','COMMAND_CENTER_WINDOWED',scenario,False)
            if n.rect(self.center) != before:
                raise Blocked('restore-geometry-disagrees')
        elif id == 'fullscreen-enter-exit':
            self.click('command-center','enter',scenario)
            self.observe('fullscreen','COMMAND_CENTER_FULLSCREEN',scenario); self.picture(scenario)
            self.click('command-center','exit',scenario)
            self.observe('button-exit','COMMAND_CENTER_WINDOWED',scenario)
            self.click('command-center','enter',scenario)
            self.observe('fullscreen-again','COMMAND_CENTER_FULLSCREEN',scenario)
            self.escape(scenario)
            self.observe('escape-exit','COMMAND_CENTER_WINDOWED',scenario)
        elif id == 'return-to-overlay':
            self.click('command-center','overlay',scenario)
            self.observe('compact','OVERLAY_COMPACT',scenario)
            self.click('overlay','toggle',scenario)
            self.observe('expanded','OVERLAY_EXPANDED',scenario)
            self.click('overlay','toggle',scenario)
            self.observe('compact-again','OVERLAY_COMPACT',scenario)
            self.request('windowed')
            self.observe('returned','COMMAND_CENTER_WINDOWED',scenario)
        elif id == 'repeated-roundtrips':
            for i in range(1,4):
                self.click('command-center','enter',scenario)
                self.observe('fullscreen-%s' % i,'COMMAND_CENTER_FULLSCREEN',scenario)
                self.click('command-center','overlay',scenario)
                self.observe('overlay-%s' % i,'OVERLAY_COMPACT',scenario)
                self.click('overlay','toggle',scenario)
                self.observe('expanded-%s' % i,'OVERLAY_EXPANDED',scenario)
                self.click('overlay','toggle',scenario)
                self.observe('compact-%s' % i,'OVERLAY_COMPACT',scenario)
                self.request('windowed')
                self.observe('roundtrip-%s' % i,'COMMAND_CENTER_WINDOWED',scenario)
        else:
            raise Blocked('unknown-scenario')
        scenario.update(target=self.target,hwnd=str(self.center),status='PASS')


def main(argv=None):
    p = argparse.ArgumentParser(); p.add_argument('--preflight',action='store_true'); p.add_argument('--root',type=Path); p.add_argument('--directory',type=Path)
    args = p.parse_args(argv)
    if args.preflight:
        Win32(); print(json.dumps({'platform':sys.platform,'ctypes':True})); return 0
    if not args.root or not args.directory:
        p.error('root and directory required')
    report = {'platform':sys.platform,'realInput':True,'preflight':{'ctypes':False},'scenarios':[{'id':id,'status':'BLOCKED','reason':'not-observed','inputs':[],'observations':[],'screenshots':[]} for id in REQUIRED], 'cleanup':{'remaining':[],'errors':[]}}
    probe = None
    try:
        probe = Probe(args.root,args.directory); report['preflight']['ctypes'] = True
        for scenario in report['scenarios']:
            try:
                probe.budget(); probe.run(scenario); scenario.pop('reason',None)
            except Blocked as e:
                scenario['reason'] = str(e)
                # Later scenarios require a known windowed baseline; do not input
                # against unknown state after a failure. Preserve all partial facts.
                for later in report['scenarios'][report['scenarios'].index(scenario)+1:]:
                    later['reason'] = 'prior-scenario-blocked:' + str(e)
                break
            except Exception as e:
                scenario.update(status='FAIL',reason=type(e).__name__+':'+str(e)); break
    except Exception as e:
        for scenario in report['scenarios']:
            scenario['reason'] = type(e).__name__+':'+str(e)
    finally:
        if probe:
            try: probe.request('quit')
            except Exception as e: report['cleanup']['errors'].append('quit:'+str(e))
        atomic_json(args.directory / 'probe.json',report)
    return 0 if all(s['status']=='PASS' for s in report['scenarios']) and not report['cleanup']['errors'] else 1

if __name__ == '__main__':
    sys.exit(main())
