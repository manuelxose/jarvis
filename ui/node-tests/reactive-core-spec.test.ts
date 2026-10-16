import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { describe, it } from 'node:test'

// Only tracked design inputs. This checks a proposal, NOT an event reducer/device.
type Spec = typeof import('../../docs/design/reactive-core-spec.json')
const spec: Spec = JSON.parse(readFileSync(new URL('../../docs/design/reactive-core-spec.json', import.meta.url), 'utf8'))
const css = readFileSync(new URL('../src/design-system/tokens.css', import.meta.url), 'utf8')
const tokens = new Map([...css.matchAll(/^\s*(--[\w-]+):\s*([^;]+);/gm)].map((m) => [m[1], m[2]]))
const states = ['IDLE', 'LISTENING', 'THINKING', 'SPEAKING', 'EXECUTING', 'SUCCESS', 'ERROR', 'DISCONNECTED', 'STARTING', 'STOPPING']
const variants = ['compact-overlay', 'expanded-overlay', 'command-center', 'startup']
const names = ['silence', 'quiet', 'normal', 'emphasis', 'pause', 'end']
const exactSet = (actual: string[], expected: string[], field: string) =>
  assert.deepEqual([...actual].sort(), [...expected].sort(), field)
const bound = (v: unknown, lo: number, hi: number, field: string) =>
  assert.ok(typeof v === 'number' && Number.isFinite(v) && v >= lo && v <= hi, `${field}: finite ${lo}..${hi}, got ${v}`)
const text = (v: unknown, field: string) => assert.ok(typeof v === 'string' && v.trim().length > 0, `${field}: required text`)
const near = (a: number, b: number, tolerance: number, field: string) =>
  assert.ok(Math.abs(a - b) <= tolerance, `${field}: ${a} != ${b}`)

function validate(s: Spec) {
  // Recursive reference/finite check catches additional token/numeric fields too.
  function walk(v: unknown, path: string) {
    if (typeof v === 'number') assert.ok(Number.isFinite(v), `${path}: nonfinite`)
    if (typeof v === 'string' && v.startsWith('--')) assert.ok(tokens.has(v), `${path}: unknown token ${v}`)
    if (v && typeof v === 'object') for (const [key, value] of Object.entries(v)) walk(value, `${path}.${key}`)
  }
  walk(s, 'spec')
  exactSet(s.states.map((v) => v.id), states, 'ten unique states')
  exactSet(s.variants.map((v) => v.id), variants, 'four unique variants')
  assert.equal(s.renderer.direction, 'svg-css-2d')
  const geometry = s.identity.geometry
  assert.deepEqual(geometry.viewBox, [-1, -1, 2, 2])
  assert.deepEqual(geometry.center, [0, 0])
  assert.equal(geometry.unit, 'half-core-side')
  bound(geometry.maxExtent, 0.8, 1, 'extent')
  bound(geometry.strokeWidth, 0.001, 0.1, 'stroke')
  bound(geometry.ringRadius, 0.1, geometry.maxExtent - geometry.strokeWidth / 2, 'radius')
  assert.equal(s.identity.transition.maxMs, 320)
  assert.deepEqual(s.identity.transition.allowedMs, [0, 120, 200, 320])
  // The root token definitions precede the reduced-motion overrides.
  for (const [i, token] of s.identity.transition.durationTokens.entries()) {
    const rootValue = [...css.matchAll(/^\s*(--[\w-]+):\s*([^;]+);/gm)].find((m) => m[1] === token)?.[2]
    assert.equal(rootValue, `${s.identity.transition.allowedMs[i + 1]}ms`, `duration token ${token}`)
  }
  exactSet(s.identity.transition.safetyStatesSnap, ['DISCONNECTED', 'STOPPING', 'ERROR'], 'snap states')
  assert.equal(new Set(s.states.map((v) => v.geometry.glyph)).size, 10, 'distinct noncolor glyphs')
  for (const state of s.states) {
    text(state.label, `${state.id} label`)
    text(state.cue, `${state.id} cue`)
    text(state.trigger, `${state.id} trigger`)
    text(state.exit, `${state.id} exit`)
    assert.ok(tokens.has(state.token), `${state.id} required accent token`)
    assert.ok(state.glow === null || tokens.has(state.glow), `${state.id} glow token`)
    text(geometry.glyphs[state.geometry.glyph as keyof typeof geometry.glyphs], `${state.id} glyph`)
    assert.ok(Object.hasOwn(geometry.dashPatterns, state.geometry.dash), `${state.id} dash`)
    assert.equal(state.geometry.ringRadius, geometry.ringRadius)
    bound(state.geometry.arcSweepDeg, 1, 360, `${state.id} sweep`)
    for (const key of ['breathPeriodMs', 'rotationPeriodMs'] as const) {
      const period = state.motion[key]
      bound(period, 0, 10000, `${state.id} ${key}`)
      assert.ok(period === 0 || period >= 1000, `${state.id} ${key}`)
    }
    bound(state.motion.breathAmplitude, 0, 0.04, `${state.id} breathAmplitude`)
    bound(state.motion.levelScaleMax, 0, 0.12, `${state.id} levelScaleMax`)
    bound(state.motion.holdMs, 0, 1500, `${state.id} holdMs`)
    assert.ok(s.identity.transition.allowedMs.includes(state.transitionInMs), `${state.id} transitionInMs`)
    if (s.identity.transition.safetyStatesSnap.includes(state.id)) assert.equal(state.transitionInMs, 0)
  }
  for (const variant of s.variants) {
    bound(variant.windowPx.width, 72, 1280, `${variant.id} width`)
    bound(variant.windowPx.height, 72, 800, `${variant.id} height`)
    bound(variant.corePx, 40, 320, `${variant.id} corePx`)
    assert.equal(variant.scaleRange.length, 2)
    variant.scaleRange.forEach((v) => bound(v, 0.6, 1.5, 'scaleRange'))
    assert.ok(variant.scaleRange[0] <= variant.scaleRange[1], 'ordered scaleRange')
    bound(variant.detailBudget.elements, 6, 24, 'element budget')
    bound(variant.detailBudget.levelRings, 1, 3, 'ring budget')
    bound(variant.detailBudget.captionLines, 1, 2, 'caption budget')
    bound(variant.legibility.minStrokePx, 2, 4, 'minStrokePx')
    bound(variant.legibility.minContrast, 3, 21, 'nontext contrast target')
    bound(variant.legibility.minTextContrast, 4.5, 21, 'text contrast target')
    bound(variant.legibility.labelPx, 13, 20, 'labelPx')
    assert.ok(variant.preserves.includes('notification placement'), 'notification preservation')
    assert.ok(variant.preserves.some((v) => /^captions?$/.test(v)), 'caption preservation')
  }
  assert.ok(s.variants[0].preserves.includes('pass-through and controls'))
  assert.ok(s.variants.find((v) => v.id === 'command-center')?.preserves.includes('navigation'))
  assert.equal(s.legacyMappings.processing, 'THINKING')
  assert.equal(s.legacyMappings.offline, 'DISCONNECTED')
  assert.equal(s.legacyMappings.executing, 'EXECUTING')
  assert.equal(s.legacyMappings.error, 'ERROR')
  assert.deepEqual([s.legacyMappings.notification.channel, s.legacyMappings.notification.state], ['notification', null])
  exactSet(s.states.find((v) => v.id === 'SUCCESS')!.forbiddenSources!, ['notification', 'speech.completed alone', 'startup mark alone'], 'success sources')
  assert.deepEqual(s.priority.order, ['DISCONNECTED', 'STOPPING', 'ERROR', 'STARTING', 'SPEAKING', 'EXECUTING', 'THINKING', 'LISTENING', 'SUCCESS', 'IDLE'])
  assert.deepEqual(s.lifecycle.guards, {
    ordering: 'strict-session-sequence', duplicatesRefreshFreshness: false,
    gapRequiresSnapshot: true, snapshotEstablishesSession: true,
    generationCancelFencesAllPlaybacks: true, terminalBeforePriority: true,
    speakingRequires: ['successful-device-write', 'same-session-generation-playback', 'fresh-output-level'],
    unavailableOutput: { static: true, level: 0, speaking: false }, invalidLevelRefreshesFreshness: false,
  }, 'lifecycle guards')
  exactSet(s.lifecycle.identity.fields, ['session', 'generation', 'playback'], 'identity fields')
  for (const event of ['activity', 'output.playback', 'output.level', 'snapshot']) {
    const input = s.lifecycle.acceptedInputs.find((v) => v.event === event)
    assert.equal(input?.availability, 'proposed', event)
    text(input?.provenance, `${event} provenance`)
  }
  const snapshot = s.lifecycle.snapshot
  assert.equal(snapshot.onInitialAndReconnect, true)
  assert.equal(snapshot.restoresStartupProgress, true)
  for (const key of ['replaysStartup', 'replaysWelcome', 'triggersCommands'] as const) assert.equal(snapshot[key], false, key)
  bound(snapshot.timeoutMs, 1, 2000, 'snapshot timeout')
  bound(s.lifecycle.freshness.levelMaxAgeMs, 1, 250, 'level freshness')
  bound(s.lifecycle.freshness.connectionLivenessBudgetMs, 1, 45000, 'liveness')
  text(s.lifecycle.freshness.onAbsent, 'absence policy')
  text(s.lifecycle.freshness.onStale, 'stale policy')

  const { level, noiseGate: gate, smoothing: smooth, cadence, peak } = s.audio
  assert.deepEqual([level.unit, level.min, level.max, smooth.unit, peak.unit, peak.max], ['normalized-rms', 0, 1, 'ms', 'normalized-peak', 1], 'audio units')
  bound(level.windowMaxMs, 1, 40, 'audio window')
  bound(gate.close, 0, 1, 'gate close')
  bound(gate.open, 0, 1, 'gate open')
  assert.ok(gate.close < gate.open, 'gate hysteresis')
  assert.equal(gate.initialOpen, false)
  bound(smooth.attackTauMs, 1, 250, 'attack tau')
  bound(smooth.releaseTauMs, smooth.attackTauMs, 1000, 'release tau')
  assert.deepEqual(smooth.dtClampMs, [0, s.lifecycle.freshness.levelMaxAgeMs], 'dt/freshness units')
  assert.equal(smooth.resetValue, 0)
  exactSet(smooth.reset, ['cancel', 'session change', 'generation change', 'playback change', 'disconnect', 'terminal end', 'stale telemetry'], 'reset causes')
  bound(peak.holdMs, 0, 120, 'peak hold')
  bound(cadence.telemetryMaxHz, 1, 30, 'telemetry cadence')
  bound(cadence.renderMaxHz, 1, 60, 'render cadence')
  assert.equal(cadence.hiddenRenderHz, 0, 'hidden render cadence')
  assert.equal(cadence.bufferSlotsPerPlayback, 1)
  assert.equal(cadence.reducedRenderOnSignalOnly, true)
  assert.equal(cadence.staticLevelRedraw, false)
  bound(s.audio.tolerance, 0.000001, 0.002, 'arithmetic tolerance')
  exactSet(s.audio.examples.map((v) => v.name), names, 'audio examples')
  for (const ex of s.audio.examples) {
    bound(ex.dtMs, 0, smooth.dtClampMs[1], `${ex.name} dtMs`)
    for (const key of ['from', 'input', 'target', 'expected'] as const) bound(ex[key], 0, 1, `${ex.name} ${key}`)
    const open = ex.gateOpenBefore ? ex.input > gate.close : ex.input >= gate.open
    assert.equal(ex.gateOpenAfter, open, `${ex.name} gate`)
    assert.equal(ex.target, open ? ex.input : 0, `${ex.name} target`)
    const tau = ex.target > ex.from ? smooth.attackTauMs : smooth.releaseTauMs
    near(ex.expected, ex.from + (ex.target - ex.from) * -Math.expm1(-ex.dtMs / tau), s.audio.tolerance, `${ex.name} time-based smoothing`)
  }
  assert.deepEqual(s.motionPolicy.precedence, ['user setting disabled', 'OS prefers-reduced-motion', 'user setting reduced', 'user setting full'])
  exactSet(s.motionPolicy.modes, ['full', 'reduced', 'disabled'], 'motion modes')
  for (const mode of ['reduced', 'disabled'] as const) {
    text(s.motionPolicy[mode], mode)
    assert.deepEqual(s.motionPolicy.staticModes[mode], { transitionMs: 0, loops: false, levelRedraw: false, glyph: true, label: true }, `${mode} static cues`)
  }
  assert.deepEqual(s.motionPolicy.adjustable.amplitudeRange, [0, 1])
  assert.deepEqual(s.motionPolicy.adjustable.durationMultiplierRange, [0.5, 1])
  assert.equal(s.motionPolicy.flash.maxFlashesPerSecond, 0)
  assert.equal(s.motionPolicy.announcements.role, 'status')
  assert.equal(s.motionPolicy.announcements.live, 'polite')
  assert.equal(s.motionPolicy.announcements.levelAnnounced, false)
  bound(s.motionPolicy.announcements.minIntervalMs, 1000, 5000, 'announcement cadence')
  exactSet(s.startup.sequences.map((v) => v.id), ['full', 'reduced', 'fast', 'muted', 'interrupted'], 'startup sequences')
  assert.equal(s.startup.sequences.find((v) => v.id === 'muted')?.kind, 'visual-only-scenario')
  assert.equal(s.startup.sequences.find((v) => v.id === 'fast')?.transitionMaxMs, 120)
  for (const phase of s.startup.phases) assert.ok(states.includes(phase.state), `${phase.id} state`)
  validateTraces(s)
}

function validateTraces(s: Spec) {
  const traces = new Map(s.traces.map((v) => [v.id, v]))
  assert.equal(traces.size, s.traces.length, 'unique trace ids')
  function trace(id: string, expectation: object) {
    const t = traces.get(id)
    assert.ok(t, `required trace ${id}`)
    assert.ok(t.steps.length > 0, `${id} stimulus`)
    for (const [key, value] of Object.entries(expectation)) assert.deepEqual(t.expect[key as keyof typeof t.expect], value, `${id}.${key}`)
    if (t.expect.diagnostic) assert.ok(s.lifecycle.freshness.diagnosticReasons.includes(t.expect.diagnostic), `${id} diagnostic`)
    return t
  }
  trace('generated-not-output', { state: 'THINKING', static: true, level: 0, diagnostic: 'level-absent' })
  const stale = trace('stale-level', { state: 'THINKING', static: true, level: 0, diagnostic: 'level-stale' })
  assert.ok(stale.steps[0].ageMs! > s.lifecycle.freshness.levelMaxAgeMs, 'stale stimulus')
  const duplicate = trace('duplicate-seq', { acceptedSequences: [], level: 0.5, refreshFreshness: false, diagnostic: 'sequence-duplicate' })
  assert.equal(duplicate.steps[0].seq, duplicate.given!.seq)
  const older = trace('out-of-order', { acceptedSequences: [], level: 0.5, diagnostic: 'sequence-out-of-order' })
  assert.ok(older.steps[0].seq! < older.given!.seq!, 'out-of-order stimulus')
  const lost = trace('disconnect-during-speaking', { state: 'DISCONNECTED', level: 0, static: true, diagnostic: 'disconnected' })
  assert.equal(lost.given!.state, 'SPEAKING')
  assert.equal(lost.steps[0].connection, 'lost')
  assert.equal(lost.steps[1].event, 'output.level')
  const cancel = trace('cancel-late-amplitude', { state: 'IDLE', level: 0, acceptedSequences: [9], diagnostic: 'identity-fenced' })
  assert.equal(cancel.steps[0].terminal, 'cancel-generation')
  assert.ok(cancel.steps.every((v) => v.generation === cancel.given!.generation), 'same cancelled generation')
  assert.ok(cancel.steps.slice(1).every((v) => v.seq! > cancel.steps[0].seq!), 'late stimuli')
  assert.notEqual(cancel.steps[2].playback, cancel.given!.playback, 'new playback cannot evade fence')
  assert.equal(cancel.steps[3].ok, true, 'late success')
  const invalid = trace('invalid-level', { rejectedCount: 3, boundedValues: [0, 1], diagnostic: 'level-invalid' })
  assert.deepEqual(invalid.steps.slice(0, 3).map((v) => v.levelEncoding), ['NaN', '+Infinity', '-Infinity'])
  assert.deepEqual(invalid.steps.slice(3).map((v) => Math.max(0, Math.min(1, v.level!))), invalid.expect.boundedValues)
  const gate = trace('gate-boundaries', { gateOpenAfterEach: [false, true, true, false], targetAfterEach: [0, 0.02, 0.015, 0] })
  let open = gate.given!.gateOpen!
  gate.steps.forEach((step, i) => {
    open = open ? step.input! > s.audio.noiseGate.close : step.input! >= s.audio.noiseGate.open
    assert.equal(open, gate.expect.gateOpenAfterEach![i], 'gate boundary arithmetic')
    assert.equal(open ? step.input : 0, gate.expect.targetAfterEach![i], 'gate target arithmetic')
  })
  const envelope = trace('envelope-cases', { terminalLevel: 0, resetGate: true })
  assert.deepEqual(envelope.steps[0].audioExamples, names)
  assert.deepEqual(envelope.expect.intermediateLevels, s.audio.examples.map((v) => v.expected))
  assert.equal(envelope.steps[1].terminal, 'playback-ended')
  const session = trace('session-replacement', { state: 'IDLE', level: 0, smoothing: 'reset', diagnostic: 'identity-fenced' })
  assert.notEqual(session.given!.session, session.steps[0].snapshotSession)
  assert.equal(session.steps[1].session, session.given!.session)
  const reconnect = trace('reconnect-snapshot', { state: 'STARTING', restoredPhase: 'announcing', level: 0, startupReplayed: false, welcomeReplayed: false, commandsTriggered: false })
  assert.deepEqual(reconnect.steps.slice(0, 2).map((v) => v.connection), ['lost', 'restored'])
  assert.equal(reconnect.steps[2].event, 'snapshot')
  assert.ok(reconnect.steps[3].seq! <= reconnect.steps[2].watermark!, 'snapshot drops old activation')
  const missing = trace('snapshot-missing', { state: 'DISCONNECTED', level: 0, diagnostic: 'snapshot-unavailable' })
  assert.ok(missing.steps[1].elapsedMs! > s.lifecycle.snapshot.timeoutMs, 'snapshot timeout stimulus')
  const partial = trace('startup-partial', { statesAfterEach: ['STARTING', 'IDLE'], apps: 'unknown', allOperational: false })
  assert.deepEqual(partial.steps.map((v) => v.phase), ['announcing', 'degraded'], 'partial phase stimuli')
  assert.equal(partial.steps[0].event, 'startup.degraded')
  assert.ok(partial.steps[0].issues!.length > 0, 'partial issues')
  const muted = trace('startup-muted', { state: 'STARTING', static: true, level: 0, kind: 'visual-only-scenario', newAudioApi: false, commandsTriggered: false })
  assert.equal(muted.steps[0].authoritativeOutputMuted, true)
  const interrupted = trace('startup-interrupted', { state: 'STOPPING', level: 0, diagnostic: 'identity-fenced', appsRolledBack: false })
  assert.equal(interrupted.steps[0].phase, 'cancelled')
  assert.equal(interrupted.steps[1].oldGeneration, true)
  trace('candidate-expired', { state: 'IDLE', startupReplayed: false, commandsTriggered: false })
  trace('startup-failed', { state: 'ERROR', persistent: true, level: 0 })
  trace('command-failed-turn-handled', { state: 'ERROR', successInferred: false })
}

describe('reactive core declarative specification (not runtime/device evidence)', () => {
  it('validates ten states, four variants, tokens, bounds, accessibility, lifecycle and arithmetic traces', () => validate(spec))
  const mutations: [string, (s: Spec) => void, RegExp][] = [
    ['missing state', (s) => { s.states.pop() }, /ten unique states/],
    ['duplicate state', (s) => { s.states[1] = structuredClone(s.states[0]) }, /ten unique states/],
    ['unknown token', (s) => { s.states[0].token = '--does-not-exist' }, /unknown token/],
    ['missing token', (s) => { Reflect.deleteProperty(s.states[0], 'token') }, /required accent token/],
    ['missing variant', (s) => { s.variants.pop() }, /four unique variants/],
    ['duplicate variant', (s) => { s.variants[1] = structuredClone(s.variants[0]) }, /four unique variants/],
    ['missing motion field', (s) => { Reflect.deleteProperty(s.states[0].motion, 'breathAmplitude') }, /breathAmplitude/],
    ['negative motion', (s) => { s.states[0].motion.breathAmplitude = -1 }, /breathAmplitude/],
    ['unbounded transition', (s) => { s.states[0].transitionInMs = 9999 }, /transitionInMs/],
    ['rapid rotation', (s) => { s.states[0].motion.rotationPeriodMs = 20 }, /rotationPeriodMs/],
    ['nonnumeric period', (s) => { Reflect.set(s.states[0].motion, 'rotationPeriodMs', '2400') }, /rotationPeriodMs/],
    ['missing label', (s) => { s.states[0].label = '' }, /label/],
    ['missing static glyph', (s) => { s.states[0].geometry.glyph = 'absent' }, /glyph/],
    ['negative attack', (s) => { s.audio.smoothing.attackTauMs = -40 }, /attack tau/],
    ['NaN attack', (s) => { s.audio.smoothing.attackTauMs = NaN }, /nonfinite/],
    ['infinite release', (s) => { s.audio.smoothing.releaseTauMs = Infinity }, /nonfinite/],
    ['negative infinity', (s) => { s.audio.peak.holdMs = -Infinity }, /nonfinite/],
    ['nonnumeric attack', (s) => { Reflect.set(s.audio.smoothing, 'attackTauMs', '40') }, /attack tau/],
    ['zero tau', (s) => { s.audio.smoothing.attackTauMs = 0 }, /attack tau/],
    ['missing audio field', (s) => { Reflect.deleteProperty(s.audio.smoothing, 'releaseTauMs') }, /release tau/],
    ['wrong units', (s) => { s.audio.smoothing.unit = 'seconds' }, /audio units/],
    ['inverted gate', (s) => { s.audio.noiseGate.close = 0.1 }, /gate hysteresis/],
    ['bad example', (s) => { s.audio.examples[1].expected = 0.9 }, /time-based smoothing/],
    ['missing silence', (s) => { s.audio.examples.shift() }, /audio examples/],
    ['unbounded render', (s) => { s.audio.cadence.renderMaxHz = 1000 }, /render cadence/],
    ['unbounded telemetry', (s) => { s.audio.cadence.telemetryMaxHz = Infinity }, /nonfinite/],
    ['negative peak', (s) => { s.audio.peak.holdMs = -1 }, /peak hold/],
    ['overlarge level scale', (s) => { s.states[3].motion.levelScaleMax = 2 }, /levelScaleMax/],
    ['disconnect loses priority', (s) => { s.priority.order.reverse() }, /DISCONNECTED/],
    ['cancel bypass', (s) => { s.lifecycle.guards.generationCancelFencesAllPlaybacks = false }, /lifecycle guards/],
    ['duplicate freshness', (s) => { s.lifecycle.guards.duplicatesRefreshFreshness = true }, /lifecycle guards/],
    ['out-of-order policy', (s) => { s.lifecycle.guards.ordering = 'wall-clock' }, /lifecycle guards/],
    ['missing output fallback', (s) => { Reflect.deleteProperty(s.lifecycle.guards, 'unavailableOutput') }, /lifecycle guards/],
    ['speaking fallback', (s) => { s.lifecycle.guards.unavailableOutput.speaking = true }, /lifecycle guards/],
    ['startup replay', (s) => { s.lifecycle.snapshot.replaysStartup = true }, /replaysStartup/],
    ['command on reconnect', (s) => { s.lifecycle.snapshot.triggersCommands = true }, /triggersCommands/],
    ['unbounded freshness', (s) => { s.lifecycle.freshness.levelMaxAgeMs = 100000 }, /level freshness/],
    ['missing reduced mode', (s) => { Reflect.deleteProperty(s.motionPolicy.staticModes, 'reduced') }, /reduced static cues/],
    ['disabled glyph loss', (s) => { s.motionPolicy.staticModes.disabled.glyph = false }, /disabled static cues/],
    ['reduced level redraw', (s) => { s.motionPolicy.staticModes.reduced.levelRedraw = true }, /reduced static cues/],
    ['OS preference bypass', (s) => { s.motionPolicy.precedence.reverse() }, /OS prefers-reduced-motion/],
    ['hidden rendering', (s) => { s.audio.cadence.hiddenRenderHz = 60 }, /hidden render cadence/],
    ['missing cancel trace', (s) => { s.traces = s.traces.filter((v) => v.id !== 'cancel-late-amplitude') }, /required trace cancel-late-amplitude/],
    ['stale level preserved', (s) => { s.traces.find((v) => v.id === 'stale-level')!.expect.level = 0.7 }, /stale-level.level/],
    ['cancel accepts amplitude', (s) => { s.traces.find((v) => v.id === 'cancel-late-amplitude')!.expect.acceptedSequences = [9, 10] }, /cancel-late-amplitude.acceptedSequences/],
    ['reconnect welcome replay', (s) => { s.traces.find((v) => v.id === 'reconnect-snapshot')!.expect.welcomeReplayed = true }, /reconnect-snapshot.welcomeReplayed/],
  ]
  for (const [name, mutate, error] of mutations) {
    it(`rejects ${name}`, () => {
      const invalid = structuredClone(spec) // Preserve NaN/Infinity in deliberate mutations.
      mutate(invalid)
      assert.throws(() => validate(invalid), error)
    })
  }
})
