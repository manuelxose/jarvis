import socket, json, urllib.request, time
s = socket.create_connection(("127.0.0.1", 47811), 3); s.sendall(b'ui\n')
ep = json.loads(s.makefile().readline()); print("ui ok", ep["ok"], "protocol", ep.get("protocol"))
def read(secs):
    r = urllib.request.urlopen(f'{ep["url"]}?token={ep["token"]}', timeout=5)
    out = []; t = time.time(); name = None
    while time.time() - t < secs:
        try: line = r.readline().decode().strip()
        except Exception: break
        if line.startswith("event:"): name = line[6:].strip()
        elif line.startswith("data:"):
            d = json.loads(line[5:]); out.append((name, d.get("kind") or d.get("name"), d.get("seq"), d.get("session","")[:6]))
    r.close(); return out
a = read(4); print("conn1", len(a), a[:6])
b = read(4); print("conn2", len(b), b[:6])
print("welcome/startup-ish in conn2:", [e for e in b if any(k in str(e) for k in ("welcome","startup","phase"))])
try:
    urllib.request.urlopen(f'{ep["url"]}?token=bad', timeout=3)
except Exception as e: print("bad token ->", e)
