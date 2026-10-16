import subprocess, sys, time, json, urllib.request, asyncio, aiohttp
exe, app = sys.argv[1], sys.argv[2]
p = subprocess.Popen([exe, app, "--remote-debugging-port=9335"], cwd=app, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    time.sleep(12)
    page = next(x for x in json.load(urllib.request.urlopen("http://127.0.0.1:9335/json", timeout=5)) if x["type"] == "page")
    async def main():
        async with aiohttp.ClientSession() as s, s.ws_connect(page["webSocketDebuggerUrl"]) as ws:
            i = 0; t0 = time.time(); last = None
            while time.time() - t0 < 100:
                i += 1
                await ws.send_json({"id": i, "method": "Runtime.evaluate", "params": {"expression": "jarvisDesktop.core.getState().then(s=>s+' | '+document.body.innerText.replace(/\\s+/g,' ').slice(0,50))", "returnByValue": True, "awaitPromise": True}})
                while True:
                    m = await ws.receive_json()
                    if m.get("id") == i: v = m["result"]["result"].get("value"); break
                if v != last: print(f"{time.time()-t0:5.1f}s {v}", flush=True); last = v
                await asyncio.sleep(1)
    asyncio.run(main())
finally:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
