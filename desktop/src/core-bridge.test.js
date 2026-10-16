'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const net = require('node:net');
const http = require('node:http');
const path = require('node:path');
const { createCoreBridge } = require('./core-bridge');

const FAST = { backoffInitialMs: 20, backoffMaxMs: 80, slowRetryMs: 150, discoveryTimeoutMs: 300, controlTimeoutMs: 300 };
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitFor(predicate, label, ms = 3000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (predicate()) return;
    await sleep(10);
  }
  assert.fail(`timed out waiting for ${label}`);
}

function listen(server, port = 0) {
  return new Promise((resolve) => server.listen(port, '127.0.0.1', () => resolve(server.address().port)));
}

// Fake Core: control socket (`ui` line) + HTTP /events (SSE) + POST /control.
function createFakeCore(opts = {}) {
  const core = {
    token: opts.token || 'tok-1',
    protocol: opts.protocol === undefined ? 1 : opts.protocol,
    header: opts.header === undefined ? '1' : opts.header,
    discoveryReply: opts.discoveryReply || null, // string override for raw reply
    discoveryDelay: false,
    staleTokens: 0,
    controlLines: [],
    controlRequests: [],
    controlStatus: 202,
    controlHang: false,
    sseResponses: new Set(),
    sockets: new Set(),
    eventsPort: 0,
    controlPort: 0,
  };
  core.events = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://x');
    if (req.method === 'POST' && url.pathname === '/control') {
      let body = '';
      req.on('data', (c) => { body += c; });
      req.on('end', () => {
        core.controlRequests.push({ headers: req.headers, body });
        if (core.controlHang) return;
        if (req.headers['x-jarvis-token'] !== core.token) { res.writeHead(401).end('{"status":"denied"}'); return; }
        res.writeHead(core.controlStatus, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ status: core.controlStatus === 202 ? 'accepted' : 'nope' }));
      });
      return;
    }
    if (url.pathname !== '/events' || url.searchParams.get('token') !== core.token) { res.writeHead(401).end(); return; }
    const head = { 'Content-Type': 'text/event-stream' };
    if (core.header !== null) head['X-Jarvis-Protocol'] = core.header;
    res.writeHead(200, head);
    res.write(': connected\n\n');
    core.sseResponses.add(res);
    res.on('close', () => core.sseResponses.delete(res));
  });
  core.control = net.createServer((socket) => {
    core.sockets.add(socket);
    socket.on('close', () => core.sockets.delete(socket));
    socket.on('error', () => {});
    let buf = '';
    socket.on('data', (chunk) => {
      buf += chunk;
      let i;
      while ((i = buf.indexOf('\n')) >= 0) {
        core.controlLines.push(buf.slice(0, i));
        buf = buf.slice(i + 1);
        if (core.discoveryDelay) continue;
        if (opts.shutdownOnQuit && core.controlLines.at(-1) === 'quit') {
          socket.end('{"ok":true}\n', () => { void core.stop(); });
          continue;
        }
        if (core.discoveryReply !== null) { socket.write(core.discoveryReply); continue; }
        let token = core.token;
        if (core.staleTokens > 0) { core.staleTokens -= 1; token = 'stale-token'; }
        const reply = { ok: true, url: `http://127.0.0.1:${core.eventsPort}/events`, token, app_url: 'x' };
        if (core.protocol !== null) reply.protocol = core.protocol;
        socket.write(`${JSON.stringify(reply)}\n`);
      }
    });
  });
  core.events.on('connection', (s) => { core.sockets.add(s); s.on('close', () => core.sockets.delete(s)); });
  core.send = (obj) => { for (const res of core.sseResponses) res.write(`data: ${JSON.stringify(obj)}\n\n`); };
  core.sendRaw = (text) => { for (const res of core.sseResponses) res.write(text); };
  core.start = async (ports = {}) => {
    core.eventsPort = await listen(core.events, ports.events || 0);
    core.controlPort = await listen(core.control, ports.control || 0);
    return core;
  };
  core.stop = async () => {
    for (const s of core.sockets) s.destroy();
    await Promise.all([new Promise((r) => core.events.close(r)), new Promise((r) => core.control.close(r))]);
  };
  return core;
}

function harness(core, extra = {}) {
  const states = [];
  const logs = [];
  const events = [];
  const bridge = createCoreBridge({ controlPort: core.controlPort, log: (r) => logs.push(r), ...FAST, ...extra });
  bridge.onState((s) => states.push(s));
  bridge.onEvent((e) => events.push(e));
  return { bridge, states, logs, events };
}

async function freePort() {
  const s = net.createServer();
  const port = await listen(s);
  await new Promise((r) => s.close(r));
  return port;
}

test('connects, forwards v1 events, ignores comments/garbage/other shapes, no duplicate emissions', async () => {
  const core = await createFakeCore().start();
  const { bridge, states, logs, events } = harness(core);
  bridge.start();
  await waitFor(() => bridge.getState() === 'connected', 'connected');
  assert.deepEqual(states, ['connected']);
  assert.equal(bridge.getState(), 'connected');
  await waitFor(() => core.sseResponses.size === 1, 'sse open');
  core.sendRaw(': keepalive\n\n');
  core.sendRaw('data: not json\n\n');
  core.sendRaw('data: [1,2]\n\n');
  core.send({ v: 1, name: 'voice.state', data: { x: 1 } });
  core.send({ v: 1, name: 5 });
  core.send({ v: 1, name: 'metrics.tick' });
  await waitFor(() => events.length === 2, 'two events');
  assert.deepEqual(events.map((e) => e.name), ['voice.state', 'metrics.tick']);
  assert.equal(bridge.getState(), 'connected');
  assert.equal(JSON.stringify(logs).includes('tok-1'), false);
  assert.equal(logs[0].state, 'connected');
  assert.equal(typeof logs[0].attempt, 'number');
  assert.deepEqual(core.controlLines, ['ui']);
  bridge.stop();
  await core.stop();
});

const snap = (session, watermark, extra = {}) => ({ v: 1, name: 'core.snapshot', session, generation: 1, watermark, age_ms: 10, output: { level: 0.5, peak: 0.6, age_ms: 4 }, microphone: null, ...extra });
const sig = (session, seq) => ({ v: 1, name: 'core.signal', session, generation: 1, seq, age_ms: 1, kind: 'turn-start', turn: 't' });

async function replayHarness() {
  const core = await createFakeCore().start();
  let t = 1000;
  const h = harness(core, { clock: () => t });
  h.bridge.start();
  await waitFor(() => h.bridge.getState() === 'connected', 'connected');
  await waitFor(() => core.sseResponses.size === 1, 'sse open');
  const sent = async (events) => { const n = h.events.length; for (const e of events) core.send(e); await waitFor(() => h.events.length === n + events.length, 'events'); };
  return { core, h, sent, advance: (ms) => { t += ms; } };
}

test('replay retains snapshot then contiguous signals, ages them by clock elapsed and never mutates stored events', async () => {
  const { core, h, sent, advance } = await replayHarness();
  assert.deepEqual(h.bridge.getReplay(), []);
  await sent([sig('s1', 1), snap('s1', 2), sig('s1', 3), sig('s1', 4)]);
  advance(250.12345);
  const first = h.bridge.getReplay();
  assert.deepEqual(first.map((e) => e.name === 'core.signal' ? e.seq : 'snap'), ['snap', 3, 4]);
  assert.equal(first[0].age_ms, 260.123);
  assert.equal(first[0].output.age_ms, 254.123);
  assert.equal(first[1].age_ms, 251.123);
  assert.equal(first[0].microphone, null);
  assert.equal(h.events[1].age_ms, 10);
  assert.equal(h.events[1].output.age_ms, 4);
  first[0].facts = 'tampered';
  assert.equal(h.bridge.getReplay()[0].facts, undefined);
  advance(100);
  assert.equal(h.bridge.getReplay()[0].age_ms, 360.123);
  h.bridge.stop();
  await core.stop();
});

test('replay drops signals at or below a newer snapshot watermark and other sessions; gaps and foreign sessions clear it', async () => {
  const { core, h, sent } = await replayHarness();
  await sent([snap('s1', 0), sig('s1', 1), sig('s1', 2), sig('s1', 3), snap('s1', 2)]);
  assert.deepEqual(h.bridge.getReplay().slice(1).map((e) => e.seq), [3]);
  await sent([sig('s1', 5)]);
  assert.deepEqual(h.bridge.getReplay(), []);
  await sent([sig('s1', 6)]);
  assert.deepEqual(h.bridge.getReplay(), []);
  await sent([snap('s1', 6), sig('s2', 7)]);
  assert.deepEqual(h.bridge.getReplay(), []);
  await sent([snap('s2', 0, { replaces_session: 's1' }), sig('s2', 1)]);
  assert.equal(h.bridge.getReplay().length, 2);
  await sent([snap(7, 3), sig('s2', 2)]);
  assert.equal(h.bridge.getReplay().length, 3);
  await sent([{ ...snap('s2', 2), watermark: 'x' }]);
  assert.equal(h.bridge.getReplay().length, 3);
  h.bridge.stop();
  await core.stop();
});

test('replay overflow past the cap clears retention and logs core-replay-dropped', async () => {
  const { core, h, sent } = await replayHarness();
  const events = [snap('s1', 0)];
  for (let i = 1; i <= 513; i++) events.push(sig('s1', i));
  await sent(events.slice(0, 513));
  assert.equal(h.bridge.getReplay().length, 513);
  await sent(events.slice(513));
  assert.deepEqual(h.bridge.getReplay(), []);
  assert.ok(h.logs.some((l) => l.event === 'core-replay-dropped' && l.reason === 'overflow'));
  h.bridge.stop();
  await core.stop();
});

test('replay is empty after the stream is lost or the bridge is stopped', async () => {
  const { core, h, sent } = await replayHarness();
  await sent([snap('s1', 0), sig('s1', 1)]);
  assert.equal(h.bridge.getReplay().length, 2);
  for (const res of core.sseResponses) res.destroy();
  await waitFor(() => h.bridge.getState() !== 'connected', 'left connected');
  assert.deepEqual(h.bridge.getReplay(), []);
  await waitFor(() => h.bridge.getState() === 'connected', 'reconnected');
  assert.deepEqual(h.bridge.getReplay(), []);
  h.bridge.stop();
  assert.deepEqual(h.bridge.getReplay(), []);
  await core.stop();
});

test('control socket refused before ever connecting -> disconnected, then attaches when Core appears', async () => {
  const port = await freePort();
  const states = [];
  const bridge = createCoreBridge({ controlPort: port, ...FAST });
  bridge.onState((s) => states.push(s));
  assert.equal(bridge.getState(), 'connecting');
  bridge.start();
  await waitFor(() => bridge.getState() === 'disconnected', 'disconnected');
  const core = createFakeCore();
  await core.start({ control: port });
  await waitFor(() => bridge.getState() === 'connected', 'connected after Core appears');
  assert.deepEqual(states, ['disconnected', 'connected']);
  bridge.stop();
  await core.stop();
});

test('discovery timeout -> disconnected', async () => {
  const core = createFakeCore();
  core.discoveryDelay = true;
  await core.start();
  const { bridge } = harness(core);
  bridge.start();
  await waitFor(() => bridge.getState() === 'disconnected', 'disconnected on timeout');
  bridge.stop();
  await core.stop();
});

test('Core restart with token rotation: reconnecting -> disconnected -> connected, never crashes', async () => {
  let core = await createFakeCore({ token: 'tok-1' }).start();
  const controlPort = core.controlPort;
  const { bridge, states } = harness(core, { reconnectingAttempts: 1 });
  bridge.start();
  await waitFor(() => bridge.getState() === 'connected', 'connected');
  await core.stop();
  await waitFor(() => bridge.getState() === 'disconnected', 'disconnected while Core absent');
  assert.deepEqual(states.slice(0, 3), ['connected', 'reconnecting', 'disconnected']);
  core = createFakeCore({ token: 'tok-2' });
  await core.start({ control: controlPort });
  await waitFor(() => bridge.getState() === 'connected', 'reconnected with rotated token');
  await waitFor(() => core.sseResponses.size === 1, 'new stream');
  assert.equal(states.at(-1), 'connected');
  bridge.stop();
  await core.stop();
});

test('stale token (401) triggers one re-discovery and recovers without auth-failure', async () => {
  const core = createFakeCore();
  core.staleTokens = 1;
  await core.start();
  const { bridge, states } = harness(core);
  bridge.start();
  await waitFor(() => bridge.getState() === 'connected', 'connected');
  assert.deepEqual(states, ['connected']);
  bridge.stop();
  await core.stop();
});

test('persistent 401 -> auth-failure with slow retry, recovers when token fixed', async () => {
  const core = createFakeCore();
  core.staleTokens = 1000;
  await core.start();
  const { bridge, states } = harness(core);
  bridge.start();
  await waitFor(() => bridge.getState() === 'auth-failure', 'auth-failure');
  const before = core.controlLines.length;
  await sleep(60);
  assert.ok(core.controlLines.length - before <= 1, 'slow retry, not a hot loop');
  core.staleTokens = 0;
  await waitFor(() => bridge.getState() === 'connected', 'recovered');
  assert.deepEqual(states, ['auth-failure', 'connected']);
  bridge.stop();
  await core.stop();
});

for (const [label, opts, reason] of [
  ['protocol missing in ui reply', { protocol: null }, 'protocol-missing'],
  ['protocol mismatch in ui reply', { protocol: 2 }, 'protocol-mismatch'],
  ['X-Jarvis-Protocol header missing', { header: null }, 'header-missing'],
  ['X-Jarvis-Protocol header mismatch', { header: '2' }, 'header-mismatch'],
]) {
  test(`incompatible-version: ${label}`, async () => {
    const core = await createFakeCore(opts).start();
    const { bridge, logs } = harness(core);
    bridge.start();
    await waitFor(() => bridge.getState() === 'incompatible-version', 'incompatible-version');
    assert.equal(logs[0].reason, reason);
    assert.equal(core.sseResponses.size, 0);
    bridge.stop();
    await core.stop();
  });
}

test('incompatible-version retries only at the slow interval and recovers', async () => {
  const core = await createFakeCore({ protocol: 2 }).start();
  const { bridge } = harness(core, { slowRetryMs: 200 });
  bridge.start();
  await waitFor(() => bridge.getState() === 'incompatible-version', 'incompatible');
  const n = core.controlLines.length;
  await sleep(100);
  assert.equal(core.controlLines.length, n, 'no fast retry');
  core.protocol = 1;
  await waitFor(() => bridge.getState() === 'connected', 'recovered after slow retry');
  bridge.stop();
  await core.stop();
});

test('event frame with another v -> incompatible-version and stream closed', async () => {
  const core = await createFakeCore().start();
  const { bridge, events } = harness(core);
  bridge.start();
  await waitFor(() => bridge.getState() === 'connected', 'connected');
  await waitFor(() => core.sseResponses.size === 1, 'sse');
  core.send({ v: 2, name: 'voice.state' });
  await waitFor(() => bridge.getState() === 'incompatible-version', 'incompatible');
  assert.equal(events.length, 0);
  await waitFor(() => core.sseResponses.size === 0, 'stream closed');
  bridge.stop();
  await core.stop();
});

for (const [label, reply] of [
  ['non-JSON', 'garbage\n'],
  ['JSON array', '[1]\n'],
  ['ok false', '{"ok":false,"error":"ui events disabled"}\n'],
  ['non-loopback url', '{"ok":true,"url":"http://10.0.0.5:9/events","token":"t","protocol":1}\n'],
  ['localhost hostname url', '{"ok":true,"url":"http://localhost:9/events","token":"t","protocol":1}\n'],
  ['wrong path', '{"ok":true,"url":"http://127.0.0.1:9/other","token":"t","protocol":1}\n'],
  ['url with query', '{"ok":true,"url":"http://127.0.0.1:9/events?x=1","token":"t","protocol":1}\n'],
  ['empty token', '{"ok":true,"url":"http://127.0.0.1:9/events","token":"","protocol":1}\n'],
  ['oversized token', `{"ok":true,"url":"http://127.0.0.1:9/events","token":"${'a'.repeat(257)}","protocol":1}\n`],
  ['non-string token', '{"ok":true,"url":"http://127.0.0.1:9/events","token":7,"protocol":1}\n'],
  ['oversized reply without newline', 'x'.repeat(5000)],
  ['oversized reply with newline', `${'x'.repeat(5000)}\n`],
]) {
  test(`rejected discovery reply: ${label} -> disconnected, never a connection`, async () => {
    const core = createFakeCore({ discoveryReply: reply });
    await core.start();
    const { bridge, states } = harness(core);
    bridge.start();
    await waitFor(() => bridge.getState() === 'disconnected', 'disconnected');
    assert.ok(!states.includes('connected'));
    bridge.stop();
    await core.stop();
  });
}

test('oversized SSE frame drops the stream (reconnecting) without forwarding', async () => {
  const core = await createFakeCore().start();
  const { bridge, events } = harness(core);
  bridge.start();
  await waitFor(() => bridge.getState() === 'connected', 'connected');
  await waitFor(() => core.sseResponses.size === 1, 'sse');
  core.sendRaw(`data: ${'x'.repeat(70 * 1024)}`);
  await waitFor(() => bridge.getState() === 'reconnecting', 'reconnecting');
  assert.equal(events.length, 0);
  bridge.stop();
  await core.stop();
});

test('control: allowlist, requires connected, token header, body, never touches control socket', async () => {
  const core = await createFakeCore().start();
  const { bridge } = harness(core);
  await assert.rejects(bridge.control('activate'), { code: 'not-connected' });
  bridge.start();
  await waitFor(() => bridge.getState() === 'connected', 'connected');
  for (const bad of ['quit', 'restart', 'status', '', null, undefined, 42, {}]) {
    await assert.rejects(bridge.control(bad), { code: 'invalid-action' });
  }
  assert.equal(core.controlRequests.length, 0);
  assert.deepEqual(await bridge.control('sleep'), { status: 'accepted' });
  assert.deepEqual(await bridge.control('activate'), { status: 'accepted' });
  const [first] = core.controlRequests;
  assert.equal(first.headers['content-type'], 'application/json');
  assert.equal(first.headers['x-jarvis-token'], 'tok-1');
  assert.deepEqual(JSON.parse(first.body), { action: 'sleep' });
  assert.deepEqual(core.controlLines, ['ui']);
  bridge.stop();
  await core.stop();
});

test('control: 401, non-202 and timeout give typed rejections without leaking the token', async () => {
  const core = await createFakeCore().start();
  const { bridge } = harness(core);
  bridge.start();
  await waitFor(() => bridge.getState() === 'connected', 'connected');
  core.token = 'rotated';
  const err401 = await bridge.control('sleep').catch((e) => e);
  assert.equal(err401.code, 'unauthorized');
  core.token = 'tok-1';
  core.controlStatus = 503;
  const err503 = await bridge.control('sleep').catch((e) => e);
  assert.equal(err503.code, 'rejected');
  core.controlStatus = 202;
  core.controlHang = true;
  const timeout = await bridge.control('activate').catch((e) => e);
  assert.equal(timeout.code, 'timeout');
  for (const e of [err401, err503, timeout]) assert.equal(e.message.includes('tok-1'), false);
  bridge.stop();
  await core.stop();
});

test('stop(): idempotent, closes sockets, clears timers, no further emissions or control', async () => {
  const core = await createFakeCore().start();
  const { bridge, states } = harness(core);
  bridge.start();
  await waitFor(() => bridge.getState() === 'connected', 'connected');
  await waitFor(() => core.sseResponses.size === 1, 'sse');
  bridge.stop();
  bridge.stop();
  await waitFor(() => core.sseResponses.size === 0, 'stream closed by stop');
  await core.stop();
  await sleep(150);
  assert.deepEqual(states, ['connected']);
  await assert.rejects(bridge.control('sleep'), { code: 'not-connected' });
  bridge.start();
  assert.deepEqual(states, ['connected']);
});

test('stop() during retry loop cancels the retry timer (no open handles)', async () => {
  const port = await freePort();
  const pending = new Set();
  const timers = {
    setTimeout: (fn, ms) => { const t = setTimeout(() => { pending.delete(t); fn(); }, ms); pending.add(t); return t; },
    clearTimeout: (t) => { pending.delete(t); clearTimeout(t); },
  };
  const bridge = createCoreBridge({ controlPort: port, timers, ...FAST });
  bridge.start();
  await waitFor(() => bridge.getState() === 'disconnected', 'disconnected');
  bridge.stop();
  await sleep(10);
  assert.equal(pending.size, 0);
});

test('source never spawns or restarts Core; quit is confined to stopCore', () => {
  const src = fs.readFileSync(path.join(__dirname, 'core-bridge.js'), 'utf8');
  assert.equal(/child_process/.test(src), false);
  assert.equal(/write\('(quit|restart|activate|sleep)/.test(src), false);
  assert.equal(src.match(/STOP_COMMAND/g).length, 2); // definition + the single write in stopCore
  assert.equal(/\b(restart)\b/.test(src.replace(/\/\/.*$/gm, '')), false);
  assert.equal(/\brequire\(/.test(src.replace(/require\('(net|http)'\)/g, '')), false);
});

test('stopCore: acknowledged shutdown reports Core disconnection while the bridge stays running', async t => {
  const core = await createFakeCore({ shutdownOnQuit: true }).start();
  const { bridge, states } = harness(core, { reconnectingAttempts: 1 });
  t.after(() => bridge.stop());
  t.after(() => core.stop());
  bridge.start();
  await waitFor(() => bridge.getState() === 'connected', 'connected before stopping Core');
  assert.deepEqual(await bridge.stopCore(), { status: 'stopping' });
  await waitFor(() => bridge.getState() === 'disconnected', 'Core stopped, desktop bridge still observing');
  assert.ok(states.includes('reconnecting'));
  assert.equal(core.controlLines.filter(line => line === 'quit').length, 1);
});

test('stopCore: sends quit exactly once over the control socket and resolves on ok', async () => {
  const core = await createFakeCore().start();
  const { bridge } = harness(core);
  const before = core.controlLines.length;
  assert.deepEqual(await bridge.stopCore(), { status: 'stopping' });
  assert.deepEqual(core.controlLines.slice(before), ['quit']);
  assert.equal(core.controlRequests.length, 0);
  bridge.stop();
  await core.stop();
});

for (const [label, reply, code] of [
  ['malformed JSON', 'garbage\n', 'rejected'],
  ['non-object', '[true]\n', 'rejected'],
  ['non-boolean ok', '{"ok":"true"}\n', 'rejected'],
  ['oversized unterminated', 'x'.repeat(5000), 'malformed'],
  ['oversized complete line', `${' '.repeat(5000)}{"ok":true}\n`, 'malformed'],
]) test(`stopCore: ${label} is rejected without unbounded buffering`, async t => {
  const core = await createFakeCore({ discoveryReply: reply }).start(); t.after(() => core.stop());
  const bridge = harness(core).bridge; t.after(() => bridge.stop());
  await assert.rejects(bridge.stopCore(), { name: 'CoreBridgeError', code });
  assert.deepEqual(core.controlLines, ['quit']);
});

test('stopCore: stop() closes an in-flight socket and settles the pending request', async t => {
  const core = await createFakeCore().start(); t.after(() => core.stop());
  core.discoveryDelay = true;
  const bridge = harness(core).bridge;
  const rejected = assert.rejects(bridge.stopCore(), { code: 'unreachable' });
  await waitFor(() => core.controlLines.includes('quit'), 'quit received');
  bridge.stop();
  await rejected;
  await waitFor(() => core.sockets.size === 0, 'stop socket closed');
});

test('stopCore: unreachable, refused and timeout give typed errors; rejected after stop()', async () => {
  const port = await freePort();
  const down = createCoreBridge({ controlPort: port, ...FAST });
  await assert.rejects(down.stopCore(), { code: 'unreachable' });
  const core = await createFakeCore({ discoveryReply: '{"ok":false}\n' }).start();
  const refused = harness(core).bridge;
  await assert.rejects(refused.stopCore(), { code: 'rejected' });
  core.discoveryDelay = true;
  const slow = harness(core, { stopTimeoutMs: 40 }).bridge;
  await assert.rejects(slow.stopCore(), { code: 'timeout' });
  slow.stop();
  await assert.rejects(slow.stopCore(), { code: 'stopped' });
  await core.stop();
});
