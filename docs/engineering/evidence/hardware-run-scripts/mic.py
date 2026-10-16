import sys, time, json, sounddevice as sd
from jarvis.observability.event_hub import EventHub
from jarvis.observability.core_signals import CoreSignals, pcm_envelope
dev = int(sys.argv[1]); dur = float(sys.argv[2])
hub = EventHub(); ev = []
hub.subscribe(lambda e: ev.append(e))
sig = CoreSignals(hub=hub)
sig.capture(True)
print("RECORDING NOW", flush=True)
t0 = time.monotonic(); rows = []
with sd.RawInputStream(samplerate=16000, channels=1, dtype="int16", blocksize=480, device=dev) as st:
    while time.monotonic() - t0 < dur:
        data, _ = st.read(480)
        rms, pk = pcm_envelope(bytes(data))
        rows.append((round(time.monotonic() - t0, 2), round(rms, 4), round(pk, 4)))
        sig.microphone(rms, pk)
sig.capture(False)
mics = [e for e in ev if "microphone" in json.dumps(e)[:200]]
print("frames", len(rows), "mic_events", len(mics), "throttled", sig.throttled, "rejected", sig.rejected)
print("rms min/max", min(r[1] for r in rows), max(r[1] for r in rows), "peak max", max(r[2] for r in rows))
print("sample event:", json.dumps(mics[len(mics)//2]) if mics else None)
print("envelope(0.5s):", [max(r[2] for r in rows if int(r[0]*2)==k) for k in range(int(dur*2))])
