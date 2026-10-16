import asyncio, json, subprocess, sys, time, urllib.request, aiohttp
import sounddevice as sd
from jarvis.observability.event_hub import EventHub, VERSION
from jarvis.observability.core_signals import CoreSignals
from jarvis.observability import event_stream
from jarvis.adapters.audio.output import AudioOutputQueue, make_sounddevice_render
from jarvis.adapters.tts.pyttsx3 import Pyttsx3TTS
from jarvis.core.turn import TurnContext
exe, app = sys.argv[1], sys.argv[2]
DEV = next(i for i, d in enumerate(sd.query_devices()) if d['max_output_channels'] and d['name'].startswith('LG HDR 4K') and sd.query_hostapis(d['hostapi'])['name'] == 'MME')
TOKEN = "m011-live-token"
T0 = time.monotonic()
def ts(): return round(time.monotonic() - T0, 2)
log = []
async def texts(s): yield s

async def main():
    hub = EventHub(); sig = CoreSignals(hub=hub)
    hub.subscribe(lambda e: log.append((ts(), "core", e.get("kind"), e.get("outcome"))) if e.get("kind") not in ("output", "microphone") else None)
    srv = await event_stream.serve_events(hub, TOKEN, port=47812, snapshot=sig.snapshot)
    reply = (json.dumps({"ok": True, "url": "http://127.0.0.1:47812/events", "token": TOKEN, "app_url": "", "protocol": VERSION}) + "\n").encode()
    async def ctl(r, w):
        await r.readline(); w.write(reply); await w.drain(); w.close()
    csrv = await asyncio.start_server(ctl, "127.0.0.1", 47811)
    p = subprocess.Popen([exe, app, "--remote-debugging-port=9337"], cwd=app, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        await asyncio.sleep(14)
        page = next(x for x in json.load(urllib.request.urlopen("http://127.0.0.1:9337/json", timeout=5)) if x["type"] == "page")
        async with aiohttp.ClientSession() as s, s.ws_connect(page["webSocketDebuggerUrl"]) as ws:
            i = 0
            async def ev(expr):
                nonlocal i; i += 1
                await ws.send_json({"id": i, "method": "Runtime.evaluate", "params": {"expression": expr, "returnByValue": True}})
                while True:
                    m = await ws.receive_json()
                    if m.get("id") == i: return m["result"]["result"].get("value")
            stop = False
            async def sample():
                last = None
                while not stop:
                    v = await ev("(()=>{const e=document.querySelector('[data-state]');return e?e.dataset.state:'-'})()")
                    if v != last: log.append((ts(), "UI", v, None)); last = v
                    await asyncio.sleep(0.05)
            st = asyncio.create_task(sample())
            async def speak(label, phrase, cancel_after=None):
                q = AudioOutputQueue(render=make_sounddevice_render(DEV, signals=sig), signals=sig)
                ctx = TurnContext.fresh("m011-live", timeout_seconds=60)
                sig.turn_start(ctx.trace_id)
                log.append((ts(), "TEST", label + " begin", None))
                task = asyncio.create_task(q.play(Pyttsx3TTS().synthesize(texts(phrase), ctx), ctx))
                if cancel_after:
                    await asyncio.sleep(cancel_after); log.append((ts(), "TEST", "flush", None)); q.flush(ctx.trace_id)
                    try: ctx.cancellation.cancel()
                    except Exception: pass
                try: await task
                except Exception as e: log.append((ts(), "TEST", type(e).__name__, None))
            await asyncio.sleep(2)
            await speak("A", "Prueba del núcleo visual. Uno, dos, tres.")
            await asyncio.sleep(4)
            await speak("B", "Esta frase larga se corta a mitad para comprobar que el núcleo deja de hablar de inmediato.", cancel_after=1.8)
            await asyncio.sleep(4)
            stop = True; await st
    finally:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
        srv.close(); csrv.close()
    for r in log: print(r)
asyncio.run(main())
