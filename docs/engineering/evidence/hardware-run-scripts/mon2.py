import socket, json, urllib.request, time, threading
def ctl(c):
    s = socket.create_connection(("127.0.0.1", 47811), 3); s.sendall((c + "\n").encode()); return json.loads(s.makefile().readline())
ep = ctl("ui"); t0 = time.time(); stop = False
def sse():
    r = urllib.request.urlopen(f"{ep['url']}?token={ep['token']}", timeout=None)
    while not stop:
        try: line = r.readline().decode().strip()
        except Exception: continue
        if not line.startswith("data:"): continue
        d = json.loads(line[5:]); k = d.get("kind") or d.get("name")
        if k in ("microphone", "output"): continue
        print(f"{time.time()-t0:5.1f}s SSE {k} {json.dumps({x:d[x] for x in d if x in ('phase','active','remaining_ms','startup','state','kind','facts')}, ensure_ascii=False)[:200]}", flush=True)
threading.Thread(target=sse, daemon=True).start()
seen_a = seen_r = 0; base = ctl("status")["listener"] if "listener" in ctl("status") else {}
base_a = {c["time"] for c in base.get("claps_accepted", [])}; base_r = {c["time"] for c in base.get("transients_rejected", [])}
while time.time() - t0 < 62:
    st = ctl("status"); l = st["listener"]
    for c in l["claps_accepted"]:
        if c["time"] not in base_a: base_a.add(c["time"]); print(f"{time.time()-t0:5.1f}s ACCEPTED {c}", flush=True)
    for c in l["transients_rejected"]:
        if c["time"] not in base_r: base_r.add(c["time"]); print(f"{time.time()-t0:5.1f}s rejected {c['reason']} peak={c['peak_dbfs']} dur={c['duration']} hf={c['hf_ratio']} snr={c['snr_db']}", flush=True)
    if st["state"] != "sentinel": print(f"{time.time()-t0:5.1f}s STATE {st['state']}", flush=True)
    time.sleep(0.4)
stop = True
