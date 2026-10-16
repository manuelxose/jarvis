"""Native Windows acceptance runner for Phase 4 (M011).

Run with the Windows venv Python (it needs sounddevice, psutil, aiohttp, comtypes):

    set PYTHONPATH=<worktree>\\src
    python native_rows.py <worktree> <out_dir> <electron.exe> [startup_report.json log_tail.txt]

Writes one sanitized sample per manifest row into <out_dir>: {"runs": [{"entry": <row id>, "status", "exit_code",
"remaining_count", "measurements"}]}. Only counts, booleans, numbers and state names are recorded: no paths, device
paths, tokens or text. Audible: row playback-envelope speaks one short phrase on the HDMI output.
"""
import asyncio, json, os, socket, subprocess, sys, tempfile, time, urllib.request
import aiohttp, psutil

WT, OUT, ELECTRON = sys.argv[1], sys.argv[2], sys.argv[3]
PY = sys.executable
LOCAL = tempfile.mkdtemp(prefix="m011-native-")
CDP = 9400
os.makedirs(OUT, exist_ok=True)


def save(row, ok, **meas):
    run = {"entry": row, "status": "PASS" if ok else "FAIL", "exit_code": 0 if ok else 1, "remaining_count": 0, "measurements": meas}
    json.dump({"runs": [run]}, open(os.path.join(OUT, row + ".json"), "w"), indent=1)
    print(f"{row}: {run['status']} {meas}", flush=True)


def ctl(cmd, timeout=3):
    s = socket.create_connection(("127.0.0.1", 47811), timeout)
    s.sendall((cmd + "\n").encode())
    return json.loads(s.makefile().readline())


def start_daemon(claps=False):
    cfg = json.load(open(os.path.join(WT, "config.win.json")))
    cfg["claps"]["enabled"] = claps
    cfg["workspace"]["startup_profile"] = cfg["workspace"]["default_profile"] = None
    cfg["memory"] = {"db_path": os.path.join(LOCAL, "jarvis.db")}
    path = os.path.join(LOCAL, "config.json")
    json.dump(cfg, open(path, "w"))
    env = dict(os.environ, PYTHONPATH=os.path.join(WT, "src"), FOR_DISABLE_CONSOLE_CTRL_HANDLER="1")
    log = open(os.path.join(LOCAL, "daemon.log"), "ab")
    p = subprocess.Popen([PY, "-m", "jarvis", "daemon", "--config", path], cwd=LOCAL, env=env, stdout=log, stderr=log,
                         creationflags=0x08000000)  # CREATE_NO_WINDOW
    for _ in range(60):
        time.sleep(1)
        try:
            if ctl("status").get("state"):
                return p
        except Exception:
            pass
    raise RuntimeError("daemon did not start")


def stop_daemon():
    try:
        ctl("quit")
    except Exception:
        pass
    time.sleep(4)


class Page:
    def __init__(self, port):
        self.port = port

    async def __aenter__(self):
        for _ in range(30):
            try:
                pages = json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json", timeout=3))
                page = next(x for x in pages if x["type"] == "page")
                break
            except Exception:
                await asyncio.sleep(1)
        self.s = aiohttp.ClientSession()
        self.ws = await self.s.ws_connect(page["webSocketDebuggerUrl"])
        self.i = 0
        return self

    async def __aexit__(self, *a):
        await self.ws.close()
        await self.s.close()

    async def ev(self, expr):
        self.i += 1
        await self.ws.send_json({"id": self.i, "method": "Runtime.evaluate", "params": {"expression": expr, "returnByValue": True, "awaitPromise": True}})
        while True:
            m = await self.ws.receive_json()
            if m.get("id") == self.i:
                return m["result"].get("result", {}).get("value")

    async def state(self):
        return await self.ev("(()=>{const e=document.querySelector('[data-state]');return e?e.dataset.state:'-'})()")


def electron(extra=()):
    global CDP
    CDP += 1
    p = subprocess.Popen([ELECTRON, os.path.join(WT, "desktop"), f"--remote-debugging-port={CDP}", *extra], cwd=os.path.join(WT, "desktop"),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return p, CDP


def kill(p):
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)


def tree_cpu(root):
    out = {}
    for pr in [root] + root.children(recursive=True):
        try:
            out[pr.pid] = (sum(pr.cpu_times()[:2]), pr.memory_full_info().uss)
        except Exception:
            pass
    return out


def row_source():
    import jarvis
    ok = os.path.normcase(os.path.abspath(jarvis.__file__)).startswith(os.path.normcase(os.path.abspath(os.path.join(WT, "src"))))
    save("runtime-source-resolution", ok, resolves_to_worktree=ok)


def row_mic():
    import sounddevice as sd
    from jarvis.observability.core_signals import CoreSignals, pcm_envelope
    from jarvis.observability.event_hub import EventHub
    hub = EventHub(); ev = []
    hub.subscribe(ev.append)
    sig = CoreSignals(hub=hub)
    frames = 0; peak = 0.0; t0 = time.monotonic()
    with sd.RawInputStream(samplerate=16000, channels=1, dtype="int16", blocksize=480) as st:
        while time.monotonic() - t0 < 4:
            data, _ = st.read(480)
            rms, pk = pcm_envelope(bytes(data)); peak = max(peak, pk); frames += 1
            sig.microphone(rms, pk)
    n = sum(1 for e in ev if e.get("kind") == "microphone")
    save("microphone-envelope", n > 20 and peak > 0, device="system default input", frames=frames, microphone_events=n, peak=round(peak, 4))


async def _texts(s):
    yield s


async def _speak(sig, device, phrase, cancel_after=None):
    from jarvis.adapters.audio.output import AudioOutputQueue, make_sounddevice_render
    from jarvis.adapters.tts.pyttsx3 import Pyttsx3TTS
    from jarvis.core.turn import TurnContext
    q = AudioOutputQueue(render=make_sounddevice_render(device, signals=sig), signals=sig)
    ctx = TurnContext.fresh("m011-native", timeout_seconds=60)
    sig.turn_start(ctx.trace_id)
    task = asyncio.create_task(q.play(Pyttsx3TTS().synthesize(_texts(phrase), ctx), ctx))
    if cancel_after:
        await asyncio.sleep(cancel_after)
        q.flush(ctx.trace_id)
        try:
            ctx.cancellation.cancel()
        except Exception:
            pass
    try:
        await task
    except Exception:
        pass


def _out_device():
    import sounddevice as sd
    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] and d["name"].startswith("LG HDR 4K") and sd.query_hostapis(d["hostapi"])["name"] == "MME":
            return i
    return None  # system default


def row_playback_and_cancel():
    from jarvis.observability.core_signals import CoreSignals
    from jarvis.observability.event_hub import EventHub
    dev = _out_device()
    res = {}
    for name, phrase, cancel in (("playback", "Prueba de aceptación de Jarvis. Uno, dos, tres.", None),
                                 ("cancel", "Esta frase larga se corta a mitad para comprobar la cancelación inmediata durante la reproducción.", 1.5)):
        hub = EventHub(); ev = []
        hub.subscribe(lambda e, ev=ev: ev.append((time.monotonic(), e)))
        sig = CoreSignals(hub=hub)
        asyncio.run(_speak(sig, dev, phrase, cancel))
        outs = [t for t, e in ev if e.get("kind") == "output"]
        ends = [(t, e.get("outcome")) for t, e in ev if e.get("kind") == "playback-end"]
        starts = [t for t, e in ev if e.get("kind") == "playback-start"]
        res[name] = dict(output_events=len(outs), playback_start=bool(starts), outcome=ends[-1][1] if ends else None,
                         outputs_after_end=sum(1 for t in outs if ends and t > ends[-1][0] + 0.001), peak=round(max([e.get("level", 0) or 0 for _, e in ev if e.get("kind") == "output"] or [0]), 4))
        time.sleep(1)
    p, c = res["playback"], res["cancel"]
    save("playback-envelope", p["output_events"] > 20 and p["playback_start"] and p["outcome"] == "ended", output="HDMI monitor" if dev is not None else "system default", **p)
    save("cancellation-envelope", c["playback_start"] and c["outcome"] == "cancelled" and c["outputs_after_end"] == 0, **c)


def sse_connect(secs=3):
    ep = ctl("ui")
    r = urllib.request.urlopen(f'{ep["url"]}?token={ep["token"]}', timeout=None)
    out = []; t = time.time()
    import threading
    def rd():
        name = None
        while time.time() - t < secs:
            line = r.readline().decode().strip()
            if line.startswith("data:"):
                d = json.loads(line[5:]); out.append((d.get("kind") or d.get("name"), d.get("session")))
    th = threading.Thread(target=rd, daemon=True); th.start(); th.join(secs + 1)
    return out, ep


def row_reconnect():
    a, ep = sse_connect(); b, _ = sse_connect()
    try:
        urllib.request.urlopen(f'{ep["url"]}?token=bad', timeout=3); bad = 200
    except urllib.error.HTTPError as e:
        bad = e.code
    kinds = lambda x: [k for k, _ in x]
    ok = kinds(a) == ["core.snapshot"] and kinds(b) == ["core.snapshot"] and a[0][1] == b[0][1] and bad == 401
    save("reconnect-no-replay", ok, connections=2, snapshots_each=[len(a), len(b)], same_session=a[0][1] == b[0][1] if a and b else False,
         startup_or_welcome_events=sum(1 for k, _ in a + b if k != "core.snapshot"), bad_token_http=bad)


async def row_idle_and_recovery():
    p, port = electron()
    try:
        async with Page(port) as pg:
            await asyncio.sleep(30)
            before = tree_cpu(psutil.Process(p.pid)); t0 = time.monotonic()
            await asyncio.sleep(60)
            after = tree_cpu(psutil.Process(p.pid)); dt = time.monotonic() - t0
            cpu = sum(after[k][0] - before[k][0] for k in after if k in before) / dt * 100 / psutil.cpu_count()
            uss = sum(v[1] for v in after.values()) / 1e6
            state = await pg.state()
            save("idle-cpu", cpu < 2 and state == "IDLE", window_s=round(dt), logical_cores=psutil.cpu_count(), cpu_percent_of_machine=round(cpu, 3), ui_state=state)
            save("idle-ram", uss < 75 and state == "IDLE", window_s=round(dt), private_mb=round(uss, 1), ui_state=state)
            # recovery: kill and relaunch the Core under the live window
            trace = [await pg.state()]
            stop_daemon(); t = time.time()
            while time.time() - t < 25:
                s = await pg.state()
                if s != trace[-1]: trace.append(s)
                await asyncio.sleep(0.5)
            start_daemon(); t = time.time()
            while time.time() - t < 60 and trace[-1] != "IDLE":
                s = await pg.state()
                if s != trace[-1]: trace.append(s)
                await asyncio.sleep(0.5)
            save("disconnect-recovery", trace[0] == "IDLE" and "DISCONNECTED" in trace and trace[-1] == "IDLE", ui_state_trace=trace)
    finally:
        kill(p)


async def row_reduced_motion():
    p, port = electron(("--force-prefers-reduced-motion",))
    try:
        async with Page(port) as pg:
            await asyncio.sleep(15)
            await pg.ev("window.__n=0;const o=window.requestAnimationFrame.bind(window);window.requestAnimationFrame=cb=>o(t=>{window.__n++;cb(t)});0")
            await asyncio.sleep(5)
            n = await pg.ev("window.__n")
            motion = await pg.ev("(()=>{const e=document.querySelector('[data-motion]');return e?e.dataset.motion:'-'})()")
            save("reduced-motion-native", motion == "reduced" and n == 0, data_motion=motion, raf_callbacks_in_5s=n)
    finally:
        kill(p)


async def row_stale():
    from jarvis.observability import event_stream
    from jarvis.observability.core_signals import CoreSignals
    from jarvis.observability.event_hub import EventHub, VERSION
    hub = EventHub(); sig = CoreSignals(hub=hub)
    srv = await event_stream.serve_events(hub, "m011-native", port=47812, snapshot=sig.snapshot)
    reply = (json.dumps({"ok": True, "url": "http://127.0.0.1:47812/events", "token": "m011-native", "app_url": "", "protocol": VERSION}) + "\n").encode()
    async def on_ctl(r, w):
        await r.readline(); w.write(reply); await w.drain(); w.close()
    csrv = await asyncio.start_server(on_ctl, "127.0.0.1", 47811)
    p, port = electron()
    try:
        async with Page(port) as pg:
            await asyncio.sleep(14)
            sig.turn_start("t1")
            stop = False
            async def pump():
                while not stop:
                    sig.output("t1", 0.5, 0.6)
                    await asyncio.sleep(0.1)
            task = asyncio.create_task(pump())
            speaking = "-"
            for _ in range(40):  # wait until the live window actually shows SPEAKING
                speaking = await pg.state()
                if speaking == "SPEAKING":
                    break
                await asyncio.sleep(0.25)
            # stale (low seq) cancel for the live turn, and foreign-session cancel: both must be ignored
            hub.publish("core.signal", session=sig.session, generation=1, seq=1, age_ms=0.0, kind="cancel-turn", turn="t1")
            hub.publish("core.signal", session="foreign", generation=9, seq=99999, age_ms=0.0, kind="cancel-turn", turn="t1")
            seen = set()
            for _ in range(12):
                await asyncio.sleep(0.1)
                seen.add(await pg.state())
            after = "SPEAKING" if seen == {"SPEAKING"} else "/".join(sorted(seen))
            stop = True; await task
            save("stale-ordering-native", speaking == "SPEAKING" and after == "SPEAKING", state_before=speaking, state_after_stale_and_foreign_events=after,
                 injected=["stale-seq cancel-turn", "foreign-session cancel-turn"])
    finally:
        kill(p); srv.close(); csrv.close()
        await asyncio.sleep(1)


def row_startup(report_path, log_path):
    """Rows startup-two-clap / startup-assets from the real run's own producer outputs (no re-run: it opens the owner's apps)."""
    rep = json.load(open(report_path)); log = open(log_path, encoding="utf-8", errors="replace").read()
    phases = [ph for ph in ("acknowledged", "initializing", "announcing", "ready") if f"startup phase: {ph}" in log]
    claps = "2 claps: confidence" in log
    save("startup-two-clap", claps and phases == ["acknowledged", "initializing", "announcing", "ready"] and rep.get("trigger") == "claps" and rep.get("phase") == "ready",
         trigger=rep.get("trigger"), final_phase=rep.get("phase"), phases_logged=phases, clap_confidence=rep.get("gesture", {}).get("confidence"), issues=len(rep.get("issues", [])))
    apps = [w for w in log.splitlines() if "workspace dev:" in w]
    launched = apps[-1].count("'launched'") if apps else 0
    save("startup-assets", rep.get("music") == "file" and rep.get("welcome_source") in ("cache", "live") and launched >= 1,
         music=rep.get("music"), welcome_source=rep.get("welcome_source"), first_sound_ms=rep["timings_ms"].get("first_sound"),
         music_started_ms=rep["timings_ms"].get("music_started"), apps_launched=launched, apps_total=apps[-1].count("': '") if apps else 0)


if __name__ == "__main__":
    if os.environ.get("ONLY") == "stale":
        asyncio.run(row_stale())
        sys.exit(0)
    row_source()
    row_mic()
    row_playback_and_cancel()
    d = start_daemon()
    try:
        row_reconnect()
        asyncio.run(row_idle_and_recovery())
        asyncio.run(row_reduced_motion())
    finally:
        stop_daemon()
    asyncio.run(row_stale())
    if len(sys.argv) > 5:
        row_startup(sys.argv[4], sys.argv[5])
    print("done")
