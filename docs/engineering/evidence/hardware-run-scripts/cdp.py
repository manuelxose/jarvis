import subprocess, sys, time, json, urllib.request, asyncio, aiohttp
exe, app = sys.argv[1], sys.argv[2]
p = subprocess.Popen([exe, app, "--remote-debugging-port=9333"], cwd=app, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    time.sleep(15)
    pages = json.load(urllib.request.urlopen("http://127.0.0.1:9333/json", timeout=5))
    print([(x["type"], x["url"][:60]) for x in pages])
    page = next(x for x in pages if x["type"] == "page")
    async def main():
        async with aiohttp.ClientSession() as s, s.ws_connect(page["webSocketDebuggerUrl"]) as ws:
            i = 0
            async def ev(expr):
                nonlocal i; i += 1
                await ws.send_json({"id": i, "method": "Runtime.evaluate", "params": {"expression": expr, "returnByValue": True, "awaitPromise": True}})
                while True:
                    m = await ws.receive_json()
                    if m.get("id") == i: return m["result"].get("result", {}).get("value", m)
            print("anims:", await ev("JSON.stringify(document.getAnimations().map(a=>({n:a.animationName||a.transitionProperty,el:(a.effect.target.className||a.effect.target.tagName)+'',st:a.playState})))"))
            await ev("window.__n=0;const o=window.requestAnimationFrame.bind(window);window.requestAnimationFrame=cb=>o(t=>{window.__n++;cb(t)});0")
            await asyncio.sleep(5)
            print("rAF/s:", (await ev("window.__n")) / 5)
            print("dom:", await ev("document.body.innerText.slice(0,200)+' | svgs='+document.querySelectorAll('svg').length+' | mode='+document.documentElement.dataset.motion"))
    asyncio.run(main())
finally:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
