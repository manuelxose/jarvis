#!/usr/bin/env node
// Owner-hardware probe. Run from the repository root; output is token-free JSON.
// Never launches the daemon or terminates a browser outside its isolated profile.
import { execFileSync } from 'node:child_process'
import { chromium } from '@playwright/test'

const report = { gate: 'unmeasured', checks: {}, samples: [], limitations: [] }
const seconds = 8
let profile, browser, launchUrl
const ps = (script, timeout = 15000) => {
  try {
    return execFileSync('powershell.exe', [
      '-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(`$ErrorActionPreference='Stop'; ${script}`, 'utf16le').toString('base64'),
    ], { encoding: 'utf8', timeout, maxBuffer: 1024 * 1024, stdio: ['ignore', 'pipe', 'pipe'] }).trim()
  } catch {
    // Node's default subprocess error includes its argv (the encoded script may contain the session token).
    throw new Error('Windows PowerShell probe failed or timed out (details intentionally redacted)')
  }
}
const win = value => value.replaceAll("'", "''")
const pause = ms => new Promise(resolve => setTimeout(resolve, ms))
const quantile = (values, q) => {
  if (!values.length) return null
  const sorted = [...values].sort((a, b) => a - b)
  return Math.round(sorted[Math.ceil(sorted.length * q) - 1] * 100) / 100
}
const summarize = values => ({ n: values.length, p50: quantile(values, .5), p95: quantile(values, .95), longFramesOver50ms: values.filter(v => v > 50).length, over16_7ms: values.filter(v => v > 16.7).length })
// Root browser process is identified by the unique profile; descendants account for render/GPU subprocesses.
const ownedPids = () => `$all=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -eq 'msedge.exe'}); $ids=@($all | Where-Object {$_.CommandLine -and $_.CommandLine.Contains('${win(profile)}')} | ForEach-Object {[int]$_.ProcessId}); do {$next=@($all | Where-Object {$ids -contains [int]$_.ParentProcessId -and $ids -notcontains [int]$_.ProcessId} | ForEach-Object {[int]$_.ProcessId}); $ids+= $next} while($next.Count -gt 0);`
const cpu = () => {
  const result = JSON.parse(ps(`${ownedPids()} $p=@(Get-Process -Id $ids -ErrorAction SilentlyContinue); @{cpuSeconds=($p | Measure-Object CPU -Sum).Sum; workingSetBytes=($p | Measure-Object WorkingSet64 -Sum).Sum; processCount=$p.Count} | ConvertTo-Json -Compress`))
  if (!result.processCount) throw new Error('Isolated Edge process tree disappeared during sampling')
  return result
}
async function sample(page, name) {
  const client = await page.context().newCDPSession(page)
  await client.send('Performance.enable')
  const before = cpu()
  const startMetrics = await client.send('Performance.getMetrics')
  const frames = await page.evaluate(async duration => {
    const intervals = []
    let previous = 0
    const started = performance.now()
    await new Promise(resolve => {
      let raf = 0
      const end = () => { clearTimeout(timer); cancelAnimationFrame(raf); resolve() }
      const timer = setTimeout(end, duration + 2000)
      const tick = now => {
        if (previous && intervals.length < 1000) intervals.push(now - previous)
        previous = now
        if (now - started < duration) raf = requestAnimationFrame(tick)
        else end()
      }
      raf = requestAnimationFrame(tick)
    })
    return intervals
  }, seconds * 1000)
  const endMetrics = await client.send('Performance.getMetrics')
  const after = cpu()
  await client.detach()
  const metrics = rows => Object.fromEntries(rows.metrics.filter(x => ['TaskDuration', 'JSHeapUsedSize', 'LayoutCount', 'RecalcStyleCount'].includes(x.name)).map(x => [x.name, x.value]))
  report.samples.push({ name, seconds, frames: summarize(frames), rawFrameIntervalsMs: frames.map(x => Math.round(x * 100) / 100), cpuSecondsDelta: after.cpuSeconds - before.cpuSeconds, workingSetBytes: after.workingSetBytes, processCount: after.processCount, performanceBefore: metrics(startMetrics), performanceAfter: metrics(endMetrics) })
}
async function main() {
  try {
    const root = process.platform === 'win32' ? process.cwd() : execFileSync('wslpath', ['-w', process.cwd()], { encoding: 'utf8', timeout: 3000 }).trim()
    const hardware = JSON.parse(ps(`$edge=@("\${env:ProgramFiles(x86)}\\Microsoft\\Edge\\Application\\msedge.exe","$env:ProgramFiles\\Microsoft\\Edge\\Application\\msedge.exe") | Where-Object {Test-Path $_} | Select-Object -First 1; @{os=(Get-CimInstance Win32_OperatingSystem).Caption;cpu=(Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name);gpu=@(Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name);edge=$edge;edgeVersion=$(if($edge){(Get-Item $edge).VersionInfo.ProductVersion}else{$null});gpuCounter=[bool](Get-Counter -ListSet 'GPU Engine' -ErrorAction SilentlyContinue)} | ConvertTo-Json -Compress -Depth 4`))
    report.environment = { os: hardware.os, cpu: hardware.cpu, gpu: hardware.gpu, edgeVersion: hardware.edgeVersion }
    report.checks.bridge = 'available'
    if (!hardware.edge) throw new Error('Edge executable unavailable on Windows')
    const listening = ps(`$cfg=Get-Content -LiteralPath '${win(root)}\\config.win.json' -Raw | ConvertFrom-Json; $port=[int]$cfg.daemon.control_port; [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)`)
    if (listening !== 'True') throw new Error('Real daemon control port not listening on Windows; no browser or command was launched')
    // The real CLI is the only authority for the per-launch token; never print its output.
    const pythonPath = process.env.JARVIS_WINDOWS_PYTHON
    if (pythonPath && !/^[\w. :\\/-]+$/.test(pythonPath)) throw new Error('Invalid JARVIS_WINDOWS_PYTHON path')
    const python = pythonPath ? `'${win(pythonPath)}'` : 'py -3'
    const cli = `Set-Location '${win(root)}'; $env:PYTHONPATH='${win(root)}\\src'; & ${python} -m jarvis --config config.win.json ui --print`
    let url
    try { url = ps(cli, 12000).split(/\r?\n/).at(-1) } catch { throw new Error('Real daemon UI CLI unavailable (daemon absent, config or Python dependency failed)') }
    if (!/^http:\/\/127\.0\.0\.1:\d+\/#token=[\w-]+$/.test(url)) throw new Error('Daemon CLI did not return a valid loopback UI URL')
    launchUrl = url
    report.checks.daemon = 'real UI URL obtained in memory (redacted)'
    profile = ps(`$d=Join-Path $env:TEMP ('jarvis-ui-probe-'+[guid]::NewGuid().ToString('N')); New-Item -ItemType Directory -Path $d | Out-Null; $d`)
    const port = 49000 + Math.floor(Math.random() * 1000)
    // Loopback debugging port grants full access to the session; isolate and keep its lifetime short.
    ps(`Start-Process -FilePath '${win(hardware.edge)}' -ArgumentList @('--user-data-dir=${win(profile)}','--remote-debugging-address=127.0.0.1','--remote-debugging-port=${port}','--window-size=1280,800','--no-first-run','--app=${win(url)}') | Out-Null`)
    let connected = false
    for (let i = 0; i < 20; i++) {
      try { browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`, { timeout: 1500 }); connected = true; break } catch { await pause(500) }
    }
    if (!connected) throw new Error('Windows Edge debugging endpoint unreachable from WSL; check WSL localhost forwarding (no Linux browser substitute)')
    const context = browser.contexts()[0]
    const page = context.pages().find(p => p.url().startsWith('http://127.0.0.1:'))
    if (!page) throw new Error('Edge app page did not open')
    await page.waitForFunction(() => document.querySelector('[data-testid="connection-status"]')?.textContent?.trim() === 'Connected', null, { timeout: 10000 })
    report.renderer = await page.locator('[data-renderer]').first().getAttribute('data-renderer')
    report.rendererIdentity = await page.locator('canvas[data-renderer="webgl2"]').first().evaluate(canvas => {
      const gl = canvas.getContext('webgl2'); if (!gl) return null
      const extension = gl.getExtension('WEBGL_debug_renderer_info')
      return extension ? gl.getParameter(extension.UNMASKED_RENDERER_WEBGL) : 'unavailable (masked)'
    }).catch(() => null)
    report.checks.gpu = report.renderer === 'webgl2' ? 'renderer observed; GPU acceleration not inferred from WebGL2 alone' : 'CSS fallback (no GPU sample)'
    report.gpuUtilization = hardware.gpuCounter ? 'counter available; per-session utilization not attributable, unmeasured' : 'GPU Engine counter unavailable; unmeasured'
    const status = () => {
      try { return JSON.parse(ps(`Set-Location '${win(root)}'; $env:PYTHONPATH='${win(root)}\\src'; & ${python} -m jarvis --config config.win.json status`, 8000)) }
      catch { return { error: 'status unavailable' } }
    }
    const confirm = async (action, label) => {
      await page.getByRole('button', { name: action, exact: true }).focus()
      await page.keyboard.press('Enter')
      await page.getByRole('dialog').getByRole('button', { name: `Confirm ${action}` }).focus()
      await pause(3000) // Give the owner time to observe the live confirmation card.
      await page.keyboard.press('Enter')
      await page.getByRole('status').filter({ hasText: `${label} request accepted by daemon` }).first().waitFor({ timeout: 10000 })
      report.checks[action.toLowerCase()] = { ui: 'keyboard confirmation accepted (not voice-turn completion)', daemonStatus: status() }
    }
    const resizeWindow = async (width, height) => {
      const client = await page.context().newCDPSession(page)
      try {
        const { windowId } = await client.send('Browser.getWindowForTarget')
        await client.send('Browser.setWindowBounds', { windowId, bounds: { windowState: 'normal', width, height } })
        return (await client.send('Browser.getWindowBounds', { windowId })).bounds
      } finally { await client.detach() }
    }
    // Only an explicit owner-side flag may dispatch real commands.
    const exercise = process.argv.includes('--exercise-controls')
    if (exercise) report.checks.beforeActivation = status()
    for (const [index, [width, height]] of [[1920, 1080], [2560, 1440]].entries()) {
      const windowBounds = await resizeWindow(width, height)
      const actualWidth = Math.min(width, windowBounds.width)
      const actualHeight = Math.min(height, windowBounds.height)
      await page.setViewportSize({ width: actualWidth, height: actualHeight })
      report.samples.push({ requestedViewport: `${width}x${height}`, viewport: `${actualWidth}x${actualHeight}`, windowBounds, devicePixelRatio: await page.evaluate(() => devicePixelRatio) })
      if (actualWidth < width || actualHeight < height) report.limitations.push(`${width}x${height}: Edge window clamped to ${actualWidth}x${actualHeight}; target resolution unmeasured`)
      await sample(page, `${actualWidth}x${actualHeight} idle (connected, no forced activity)`)
      if (index === 0 && exercise) await confirm('Activate', 'Activation')
      const orbState = () => page.locator('[data-orb-state]').first().getAttribute('data-orb-state')
      // After Activate, wait for the welcome speech so a genuine speaking frame sample can be taken.
      if (index === 0 && exercise) await page.waitForFunction(() => ['speaking', 'listening'].includes(document.querySelector('[data-orb-state]')?.getAttribute('data-orb-state')), null, { timeout: 75000 }).catch(() => {})
      const state = await orbState()
      if (state === 'speaking' || state === 'listening') await sample(page, `${width}x${height} observed ${state}`)
      else report.limitations.push(`${width}x${height}: active/speaking unmeasured; no live event during window`)
    }
    if (exercise) await confirm('Sleep', 'Sleep')
    else report.limitations.push('Real commands not issued: pass --exercise-controls with owner present; verify audible output independently')
    report.gate = 'partial: check active state, GPU utilization and owner confirmation before acceptance'
  } catch (error) {
    report.limitations.push(String(error.message).replaceAll(launchUrl || '\u0000', '[REDACTED_URL]').replace(/#token=[^\s]+/g, '#token=[REDACTED]'))
  } finally {
    await browser?.close().catch(() => {})
    if (profile) {
      try {
        // Stop only this profile's Edge browser and descendants, not other owner sessions.
        ps(`${ownedPids()} $ids | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }; Start-Sleep -Milliseconds 500; Remove-Item -LiteralPath '${win(profile)}' -Recurse -Force -ErrorAction SilentlyContinue`, 15000)
      } catch { report.limitations.push('Isolated Edge cleanup failed; remove temporary jarvis-ui-probe profile after confirming its processes ended') }
    }
    console.log(JSON.stringify(report, null, 2).replace(/#token=[^\s"\\]+/g, '#token=[REDACTED]'))
  }
}
await main()
if (report.gate === 'unmeasured') process.exitCode = 2
