import asyncio, json, time
from jarvis.observability.event_hub import EventHub
from jarvis.observability.core_signals import CoreSignals
from jarvis.adapters.audio.output import AudioOutputQueue, make_sounddevice_render
from jarvis.adapters.tts.pyttsx3 import Pyttsx3TTS
from jarvis.core.turn import TurnContext

import sounddevice as sd
DEV = next(i for i,d in enumerate(sd.query_devices()) if d['max_output_channels'] and d['name'].startswith('Auriculares (G435') and sd.query_hostapis(d['hostapi'])['name']=='MME')
print('device', DEV, sd.query_devices(DEV)['name'], flush=True)
time.sleep(10)
async def texts(s):
    yield s

async def run(label, phrase, cancel_after=None):
    hub = EventHub(); ev = []
    hub.subscribe(lambda e: ev.append((time.monotonic(), e)))
    sig = CoreSignals(hub=hub)
    q = AudioOutputQueue(render=make_sounddevice_render(DEV, signals=sig), signals=sig)
    ctx = TurnContext.fresh("m011-check4", timeout_seconds=60)
    sig.turn_start(ctx.trace_id)
    tts = Pyttsx3TTS()
    t0 = time.monotonic()
    task = asyncio.create_task(q.play(tts.synthesize(texts(phrase), ctx), ctx))
    tf = None
    if cancel_after:
        await asyncio.sleep(cancel_after); tf = time.monotonic(); q.flush(ctx.trace_id); ctx.cancellation.cancel() if hasattr(ctx.cancellation, "cancel") else None
    try: await task
    except Exception as e: print(label, "task ended with", type(e).__name__)
    kinds = [(round(t - t0, 2), e.get("kind"), e.get("outcome"), e.get("level")) for t, e in ev]
    outs = [k for k in kinds if k[1] == "output"]
    ends = [k for k in kinds if k[1] == "playback" or k[2]]
    print(label, "| output events:", len(outs), "| first", outs[0][0] if outs else None, "last", outs[-1][0] if outs else None,
          "| max level", max((o[3] or 0) for o in outs) if outs else None)
    print(label, "| non-output kinds:", [k[:3] for k in kinds if k[1] != "output"])
    if tf: print(label, "| flush at", round(tf - t0, 2), "s; last output after flush:", round((ev[-1][0] if outs else tf) - tf, 3) if outs else None)

async def main():
    await run("A-natural", "Prueba de audio de Jarvis. Uno, dos, tres.")
    await asyncio.sleep(1)
    await run("A2", "Segunda prueba. Si me oyes, Jarvis está funcionando."); await asyncio.sleep(1)
    return
    await run("B-cancel", "Esta es una frase larga para comprobar la cancelación inmediata durante la reproducción, que debería cortarse enseguida.", cancel_after=1.5)
asyncio.run(main())
