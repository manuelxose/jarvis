'use strict';

// Attach to an already-running Jarvis Core (D053). Never spawns or restarts Core.
// Only the explicit main-process tray confirmation may call stopCore().
const net = require('net');
const http = require('http');

const PROTOCOL = 1;
const HOST = '127.0.0.1';
const DISCOVERY_MAX_BYTES = 4096;
const FRAME_MAX_BYTES = 64 * 1024;
const TOKEN_MAX = 256;
const ACTIONS = ['activate', 'sleep'];
const STOP_COMMAND = 'quit'; // control-socket line; reachable only via stopCore()
const RETAIN_MAX = 512;
const URL_RE = /^http:\/\/127\.0\.0\.1:(\d{1,5})\/events$/;

class BridgeError extends Error {
  constructor(code, message) {
    super(message);
    this.name = 'CoreBridgeError';
    this.code = code;
  }
}

function parseDiscovery(line) {
  let reply;
  try {
    reply = JSON.parse(line);
  } catch {
    return { fail: 'malformed', reason: 'discovery-not-json' };
  }
  if (!reply || typeof reply !== 'object' || Array.isArray(reply)) return { fail: 'malformed', reason: 'discovery-not-object' };
  if (reply.ok !== true) return { fail: 'unreachable', reason: 'ui-endpoint-unavailable' };
  const match = typeof reply.url === 'string' ? URL_RE.exec(reply.url) : null;
  const port = match ? Number(match[1]) : 0;
  if (!match || port < 1 || port > 65535) return { fail: 'malformed', reason: 'discovery-bad-url' };
  if (typeof reply.token !== 'string' || !reply.token || reply.token.length > TOKEN_MAX) return { fail: 'malformed', reason: 'discovery-bad-token' };
  if (reply.protocol === undefined) return { fail: 'incompatible', reason: 'protocol-missing' };
  if (reply.protocol !== PROTOCOL) return { fail: 'incompatible', reason: 'protocol-mismatch' };
  return { info: { url: reply.url, token: reply.token, port } };
}

function createCoreBridge(options = {}) {
  const {
    controlPort = 47811,
    timers = { setTimeout, clearTimeout },
    log = () => {},
    now = Date.now,
    clock = () => performance.now(),
    backoffInitialMs = 500,
    backoffMaxMs = 10000,
    slowRetryMs = 30000,
    discoveryTimeoutMs = 3000,
    controlTimeoutMs = 7000,
    stopTimeoutMs = 3000,
    reconnectingAttempts = 2,
  } = options;

  const stateListeners = new Set();
  const eventListeners = new Set();
  let state = 'connecting';
  let started = false;
  let stopped = false;
  let everConnected = false;
  let failures = 0;
  let authRejections = 0;
  let attempt = 0;
  let info = null;
  let session = 0;
  let retryTimer = null;
  const live = new Set(); // sockets/requests to destroy on stop or session change
  let retained = null; // { snapshot: {event, at}, signals: [{event, at}] } for late-mounting windows (D059)

  function retain(event) {
    const at = clock();
    if (event.name === 'core.snapshot') {
      if (typeof event.session !== 'string' || !Number.isInteger(event.watermark)) return;
      const kept = retained ? retained.signals.filter((s) => s.event.session === event.session && s.event.seq > event.watermark) : [];
      retained = { snapshot: { event, at }, signals: kept };
    } else if (event.name === 'core.signal' && retained) {
      const last = retained.signals.length ? retained.signals[retained.signals.length - 1].event.seq : retained.snapshot.event.watermark;
      if (event.session !== retained.snapshot.event.session || !Number.isInteger(event.seq) || event.seq !== last + 1) {
        retained = null;
        return;
      }
      retained.signals.push({ event, at });
      if (retained.signals.length > RETAIN_MAX) {
        retained = null;
        try { log({ event: 'core-replay-dropped', reason: 'overflow', at: now() }); } catch { /* logging must never break the bridge */ }
      }
    }
  }

  function aged(entry, current) {
    const elapsed = current - entry.at;
    const bump = (v) => Math.round((v + elapsed) * 1000) / 1000;
    const copy = { ...entry.event };
    if (typeof copy.age_ms === 'number') copy.age_ms = bump(copy.age_ms);
    for (const key of ['output', 'microphone']) {
      if (copy[key] && typeof copy[key] === 'object' && typeof copy[key].age_ms === 'number') copy[key] = { ...copy[key], age_ms: bump(copy[key].age_ms) };
    }
    return copy;
  }

  function setState(next, reason) {
    if (stopped || next === state) return;
    state = next;
    if (next !== 'connected') retained = null;
    try {
      log({ state, reason, attempt, at: now() });
    } catch {
      // logging must never break the bridge
    }
    for (const fn of [...stateListeners]) {
      try { fn(state, { reason, attempt }); } catch { /* listener errors are isolated */ }
    }
  }

  function track(resource) {
    live.add(resource);
    resource.once('close', () => live.delete(resource));
    return resource;
  }

  function closeLive() {
    for (const resource of live) resource.destroy();
    live.clear();
  }

  function schedule(delay) {
    if (stopped) return;
    timers.clearTimeout(retryTimer);
    retryTimer = timers.setTimeout(run, delay);
    if (retryTimer && typeof retryTimer.unref === 'function') retryTimer.unref();
  }

  function backoff() {
    return Math.min(backoffInitialMs * 2 ** Math.max(0, failures - 1), backoffMaxMs);
  }

  function fail(kind, reason, mySession) {
    if (stopped || mySession !== session) return;
    info = null;
    if (kind === 'incompatible') {
      setState('incompatible-version', reason);
      schedule(slowRetryMs);
      return;
    }
    if (kind === 'unauthorized') {
      authRejections += 1;
      if (authRejections >= 2) {
        setState('auth-failure', reason);
        schedule(slowRetryMs);
      } else {
        schedule(backoffInitialMs); // re-discover once: Core restart rotates the token
      }
      return;
    }
    failures += 1;
    const soft = everConnected && failures <= reconnectingAttempts;
    setState(soft ? 'reconnecting' : 'disconnected', reason);
    schedule(backoff());
  }

  function discover(onDone) {
    let buffer = '';
    let finished = false;
    const socket = track(net.createConnection({ host: HOST, port: controlPort }));
    const timer = timers.setTimeout(() => done({ fail: 'unreachable', reason: 'discovery-timeout' }), discoveryTimeoutMs);
    function done(result) {
      if (finished) return;
      finished = true;
      timers.clearTimeout(timer);
      socket.destroy();
      onDone(result);
    }
    socket.setEncoding('utf8');
    socket.on('connect', () => socket.write('ui\n'));
    socket.on('data', (chunk) => {
      buffer += chunk;
      const newline = buffer.indexOf('\n');
      if (newline >= 0) done(newline > DISCOVERY_MAX_BYTES ? { fail: 'malformed', reason: 'discovery-too-large' } : parseDiscovery(buffer.slice(0, newline)));
      else if (buffer.length > DISCOVERY_MAX_BYTES) done({ fail: 'malformed', reason: 'discovery-too-large' });
    });
    socket.on('error', () => done({ fail: 'unreachable', reason: 'control-socket-unreachable' }));
    socket.on('close', () => done({ fail: 'unreachable', reason: 'control-socket-closed' }));
  }

  function openStream(found, mySession) {
    const req = track(http.get({
      host: HOST,
      port: found.port,
      path: `/events?token=${encodeURIComponent(found.token)}`,
      headers: { Accept: 'text/event-stream' },
    }));
    let connected = false;
    let ended = false;
    function lost(kind, reason) {
      if (ended) return;
      ended = true;
      req.destroy();
      if (mySession !== session || stopped) return;
      if (connected) {
        failures = 0;
        info = null;
        setState('reconnecting', reason);
        schedule(backoffInitialMs);
      } else {
        fail(kind, reason, mySession);
      }
    }
    req.on('error', () => lost('unreachable', 'events-unreachable'));
    req.on('response', (res) => {
      if (mySession !== session || stopped) return res.destroy();
      if (res.statusCode === 401) { res.resume(); return lost('unauthorized', 'token-rejected'); }
      if (res.statusCode !== 200) { res.resume(); return lost('unreachable', `events-http-${res.statusCode}`); }
      const header = res.headers['x-jarvis-protocol'];
      if (header === undefined) return lost('incompatible', 'header-missing');
      if (header !== String(PROTOCOL)) return lost('incompatible', 'header-mismatch');
      connected = true;
      everConnected = true;
      failures = 0;
      authRejections = 0;
      info = found;
      setState('connected', 'stream-open');
      let buffer = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => {
        buffer += chunk;
        let newline;
        while ((newline = buffer.indexOf('\n')) >= 0) {
          const line = buffer.slice(0, newline).replace(/\r$/, '');
          buffer = buffer.slice(newline + 1);
          if (line.length > FRAME_MAX_BYTES) return frameViolation('frame-too-large');
          if (line.startsWith('data:')) {
            if (!handleFrame(line.slice(5).trim())) return;
          }
        }
        if (buffer.length > FRAME_MAX_BYTES) frameViolation('frame-too-large');
      });
      res.on('end', () => lost('unreachable', 'stream-ended'));
      res.on('error', () => lost('unreachable', 'stream-error'));
      res.on('close', () => lost('unreachable', 'stream-closed'));
    });

    function frameViolation(reason) {
      lost('unreachable', reason);
    }

    function handleFrame(payload) {
      let event;
      try {
        event = JSON.parse(payload);
      } catch {
        return true;
      }
      if (!event || typeof event !== 'object' || Array.isArray(event) || event.v === undefined) return true;
      if (event.v !== PROTOCOL) {
        // connected stays true so lost() would report reconnecting; mark incompatible explicitly
        ended = true;
        req.destroy();
        if (mySession === session && !stopped) {
          info = null;
          setState('incompatible-version', 'event-version-mismatch');
          schedule(slowRetryMs);
        }
        return false;
      }
      if (typeof event.name !== 'string') return true;
      retain(event);
      for (const fn of [...eventListeners]) {
        try { fn(event); } catch { /* listener errors are isolated */ }
      }
      return true;
    }
  }

  function run() {
    if (stopped) return;
    retryTimer = null;
    closeLive();
    session += 1;
    attempt += 1;
    const mySession = session;
    discover((result) => {
      if (stopped || mySession !== session) return;
      if (result.fail) return fail(result.fail, result.reason, mySession);
      openStream(result.info, mySession);
    });
  }

  return {
    start() {
      if (started || stopped) return;
      started = true;
      run();
    },
    stop() {
      if (stopped) return;
      stopped = true;
      retained = null;
      timers.clearTimeout(retryTimer);
      retryTimer = null;
      session += 1;
      closeLive();
      stateListeners.clear();
      eventListeners.clear();
    },
    getState: () => state,
    getReplay() {
      if (stopped || state !== 'connected' || !retained) return [];
      const current = clock();
      return [aged(retained.snapshot, current), ...retained.signals.map((s) => aged(s, current))];
    },
    onState(fn) {
      stateListeners.add(fn);
      return () => stateListeners.delete(fn);
    },
    onEvent(fn) {
      eventListeners.add(fn);
      return () => eventListeners.delete(fn);
    },
    // The only Core-stopping path. Deliberately not in ACTIONS and never wired to renderer IPC.
    stopCore() {
      if (stopped) return Promise.reject(new BridgeError('stopped', 'bridge is stopped'));
      return new Promise((resolve, reject) => {
        let settled = false;
        let buffer = '';
        const socket = track(net.createConnection({ host: HOST, port: controlPort }));
        const finish = (fn, value) => {
          if (settled) return;
          settled = true;
          timers.clearTimeout(timer);
          socket.destroy();
          fn(value);
        };
        const timer = timers.setTimeout(() => finish(reject, new BridgeError('timeout', 'stop request timed out')), stopTimeoutMs);
        socket.setEncoding('utf8');
        socket.on('connect', () => socket.write(`${STOP_COMMAND}\n`));
        socket.on('data', (chunk) => {
          buffer += chunk;
          const newline = buffer.indexOf('\n');
          if (newline < 0) {
            if (buffer.length > DISCOVERY_MAX_BYTES) finish(reject, new BridgeError('malformed', 'stop reply too large'));
            return;
          }
          if (newline > DISCOVERY_MAX_BYTES) return finish(reject, new BridgeError('malformed', 'stop reply too large'));
          let reply = null;
          try { reply = JSON.parse(buffer.slice(0, newline)); } catch { /* rejected below */ }
          if (reply && reply.ok === true) finish(resolve, { status: 'stopping' });
          else finish(reject, new BridgeError('rejected', 'Core refused to stop'));
        });
        socket.on('error', () => finish(reject, new BridgeError('unreachable', 'control socket unreachable')));
        socket.on('close', () => finish(reject, new BridgeError('unreachable', 'control socket closed')));
      });
    },
    control(action) {
      if (typeof action !== 'string' || !ACTIONS.includes(action)) return Promise.reject(new BridgeError('invalid-action', 'unsupported control action'));
      if (stopped || state !== 'connected' || !info) return Promise.reject(new BridgeError('not-connected', 'Jarvis Core is not connected'));
      const target = info;
      const body = JSON.stringify({ action });
      return new Promise((resolve, reject) => {
        let settled = false;
        const finish = (fn, value) => {
          if (settled) return;
          settled = true;
          timers.clearTimeout(timer);
          fn(value);
        };
        const req = http.request({
          host: HOST,
          port: target.port,
          path: '/control',
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Content-Length': Buffer.byteLength(body),
            'X-Jarvis-Token': target.token,
          },
        });
        const timer = timers.setTimeout(() => {
          req.destroy();
          finish(reject, new BridgeError('timeout', 'control request timed out'));
        }, controlTimeoutMs);
        track(req);
        req.on('error', () => finish(reject, new BridgeError('unreachable', 'control request failed')));
        req.on('response', (res) => {
          let text = '';
          res.setEncoding('utf8');
          res.on('data', (chunk) => { if (text.length < 1024) text += chunk; });
          res.on('end', () => {
            let parsed = null;
            try { parsed = JSON.parse(text); } catch { /* handled below */ }
            if (res.statusCode === 202 && parsed && parsed.status === 'accepted') return finish(resolve, { status: 'accepted' });
            const code = res.statusCode === 401 ? 'unauthorized' : 'rejected';
            finish(reject, new BridgeError(code, `control rejected (${res.statusCode})`));
          });
          res.on('error', () => finish(reject, new BridgeError('unreachable', 'control response failed')));
        });
        req.end(body);
      });
    },
  };
}

module.exports = { createCoreBridge, BridgeError, PROTOCOL };
