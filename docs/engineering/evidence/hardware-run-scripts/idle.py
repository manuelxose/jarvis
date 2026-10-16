import subprocess, sys, time, json, psutil
exe, app, secs = sys.argv[1], sys.argv[2], float(sys.argv[3])
p = subprocess.Popen([exe, app], cwd=app, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
root = psutil.Process(p.pid)
def tree():
    try: return [root] + root.children(recursive=True)
    except psutil.NoSuchProcess: return []
time.sleep(30)
def snap():
    out = {}
    for pr in tree():
        try: out[pr.pid] = (pr.cpu_times(), pr.memory_info().rss, pr.memory_full_info().uss, pr.cmdline())
        except Exception: pass
    return out
a = snap(); t0 = time.monotonic(); time.sleep(secs); b = snap(); dt = time.monotonic() - t0
core = [x for x in psutil.process_iter(['pid','name','cmdline']) if x.info['cmdline'] and 'jarvis' in ' '.join(x.info['cmdline']) and 'daemon' in ' '.join(x.info['cmdline'])]
rows = []
for pid, (ct, rss, uss, cmd) in b.items():
    if pid in a:
        cpu = ((ct.user + ct.system) - (a[pid][0].user + a[pid][0].system)) / dt * 100
        typ = next((c.split('=')[1] for c in cmd if c.startswith('--type=')), 'main')
        rows.append((pid, typ, round(cpu, 2), round(uss/1e6, 1), round(rss/1e6, 1)))
for r in rows: print("electron", r)
print("TOTAL electron cpu%", round(sum(r[2] for r in rows), 2), "uss MB", round(sum(r[3] for r in rows), 1), "rss MB", round(sum(r[4] for r in rows), 1), "window s", round(dt))
for c in core:
    try: print("core pid", c.pid, "rss MB", round(c.memory_info().rss/1e6))
    except Exception: pass
subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
