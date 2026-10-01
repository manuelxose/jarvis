import { useEffect, useRef, useState } from 'react'
import type { AssistantState } from '../../foundations/StatusIndicator'
import { VisualCorePlaceholder } from '../VisualCorePlaceholder'
import { stepParams, targetParams, type OrbSignal } from './orbModel'

const VERT = `#version 300 es
void main() {
  vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}`

const FRAG = `#version 300 es
precision highp float;
uniform float u_time, u_hue, u_energy, u_turbulence;
uniform vec2 u_resolution;
out vec4 o;
float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float noise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1, 0)), f.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), f.x), f.y);
}
vec3 hsv(float h, float s, float v) {
  vec3 k = clamp(abs(fract(h + vec3(0.0, 2.0 / 3.0, 1.0 / 3.0)) * 6.0 - 3.0) - 1.0, 0.0, 1.0);
  return v * mix(vec3(1.0), k, s);
}
void main() {
  vec2 uv = (gl_FragCoord.xy * 2.0 - u_resolution) / min(u_resolution.x, u_resolution.y);
  float r = length(uv), a = atan(uv.y, uv.x);
  float t = u_time * (0.4 + u_turbulence * 1.6);
  float wob = noise(vec2(cos(a), sin(a)) * (1.5 + u_turbulence * 2.0) + t) - 0.5;
  float pulse = sin(u_time * (1.5 + u_energy * 3.0)) * u_energy * 0.05;
  float radius = 0.5 + pulse + wob * u_turbulence * 0.35;
  float d = r - radius;
  float core = pow(clamp(1.0 - r / radius, 0.0, 1.0), 1.5) * (0.6 + 0.8 * u_energy);
  float body = smoothstep(0.03, -0.03, d) * (0.35 + core);
  float ring = exp(-abs(d) * 40.0) * 0.5 * u_energy;
  float glow = exp(-max(d, 0.0) * 5.0) * 0.45 * (0.4 + u_energy) * smoothstep(1.0, 0.7, r);
  float alpha = clamp(body + ring + glow, 0.0, 1.0);
  vec3 col = hsv(u_hue, 0.3 + 0.5 * smoothstep(0.05, 0.3, u_energy), 1.0);
  col = mix(col, vec3(1.0), core * 0.5);
  o = vec4(col * alpha, alpha);
}`

const GL_OPTIONS: WebGLContextAttributes = { alpha: true, premultipliedAlpha: true, antialias: false, powerPreference: 'low-power' }

// Pending lose_context calls keyed by canvas: a StrictMode remount reuses the canvas and must cancel the loss.
const pendingLose = new WeakMap<HTMLCanvasElement, number>()

function compile(gl: WebGL2RenderingContext, type: number, source: string): WebGLShader {
  const shader = gl.createShader(type)
  if (!shader) throw new Error('createShader failed')
  gl.shaderSource(shader, source)
  gl.compileShader(shader)
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const log = gl.getShaderInfoLog(shader)
    gl.deleteShader(shader)
    throw new Error(`shader compile failed: ${log}`)
  }
  return shader
}

interface JarvisOrbProps {
  state: AssistantState
  speaking: boolean
  size?: number
}

/** Raw-WebGL2 orb that reacts to the assistant state; falls back to the CSS core when WebGL2 is unavailable. */
export function JarvisOrb({ state, speaking, size = 120 }: JarvisOrbProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const signalRef = useRef<OrbSignal>({ state, speaking })
  const syncRef = useRef<(() => void) | null>(null)
  const warnedRef = useRef(false)
  const [fallback, setFallback] = useState(false)

  useEffect(() => {
    signalRef.current = { state, speaking }
    syncRef.current?.()
  }, [state, speaking])

  useEffect(() => {
    if (fallback) return
    const canvas = canvasRef.current
    if (!canvas) return

    const switchToFallback = (reason: string) => {
      if (!warnedRef.current) {
        warnedRef.current = true
        console.warn(`JarvisOrb: ${reason}; using the CSS fallback`)
      }
      setFallback(true)
    }

    const pending = pendingLose.get(canvas)
    if (pending !== undefined) {
      clearTimeout(pending)
      pendingLose.delete(canvas)
    }

    const gl = canvas.getContext('webgl2', GL_OPTIONS)
    if (!gl) {
      switchToFallback('WebGL2 unavailable')
      return
    }

    let vs: WebGLShader | null = null
    let fs: WebGLShader | null = null
    let program: WebGLProgram | null = null
    let vao: WebGLVertexArrayObject | null = null
    const release = () => {
      if (program) gl.deleteProgram(program)
      if (vs) gl.deleteShader(vs)
      if (fs) gl.deleteShader(fs)
      if (vao) gl.deleteVertexArray(vao)
    }

    try {
      vs = compile(gl, gl.VERTEX_SHADER, VERT)
      fs = compile(gl, gl.FRAGMENT_SHADER, FRAG)
      program = gl.createProgram()
      if (!program) throw new Error('createProgram failed')
      gl.attachShader(program, vs)
      gl.attachShader(program, fs)
      gl.linkProgram(program)
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
        throw new Error(`program link failed: ${gl.getProgramInfoLog(program)}`)
      }
      vao = gl.createVertexArray()
    } catch (error) {
      release()
      switchToFallback(error instanceof Error ? error.message : String(error))
      return
    }

    gl.useProgram(program)
    gl.bindVertexArray(vao)
    gl.viewport(0, 0, canvas.width, canvas.height)
    const uTime = gl.getUniformLocation(program, 'u_time')
    const uHue = gl.getUniformLocation(program, 'u_hue')
    const uEnergy = gl.getUniformLocation(program, 'u_energy')
    const uTurbulence = gl.getUniformLocation(program, 'u_turbulence')
    gl.uniform2f(gl.getUniformLocation(program, 'u_resolution'), canvas.width, canvas.height)

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)')
    const t0 = performance.now()
    let target = targetParams(signalRef.current)
    let params = target
    let raf = 0
    let last = 0
    let lost = false

    const draw = (seconds: number) => {
      if (lost) return
      gl.uniform1f(uTime, seconds)
      gl.uniform1f(uHue, params.hue)
      gl.uniform1f(uEnergy, params.energy)
      gl.uniform1f(uTurbulence, params.turbulence)
      gl.drawArrays(gl.TRIANGLES, 0, 3)
    }
    const tick = (now: number) => {
      raf = requestAnimationFrame(tick)
      const dt = last ? (now - last) / 1000 : 0
      last = now
      params = stepParams(params, target, dt)
      draw((now - t0) / 1000)
    }
    const stop = () => {
      cancelAnimationFrame(raf)
      raf = 0
    }
    const start = () => {
      if (raf || lost || reduced.matches || document.hidden) return
      last = 0
      raf = requestAnimationFrame(tick)
    }
    const sync = () => {
      target = targetParams(signalRef.current)
      if (reduced.matches) {
        params = target
        draw(0)
      }
    }
    const onMotionChange = () => {
      if (reduced.matches) {
        stop()
        sync()
      } else {
        start()
      }
    }
    const onVisibility = () => (document.hidden ? stop() : start())
    const onLost = (event: Event) => {
      event.preventDefault()
      lost = true
      stop()
      switchToFallback('WebGL context lost')
    }

    canvas.addEventListener('webglcontextlost', onLost)
    reduced.addEventListener('change', onMotionChange)
    document.addEventListener('visibilitychange', onVisibility)
    syncRef.current = sync
    sync()
    if (!reduced.matches) draw(0)
    start()

    return () => {
      syncRef.current = null
      stop()
      canvas.removeEventListener('webglcontextlost', onLost)
      reduced.removeEventListener('change', onMotionChange)
      document.removeEventListener('visibilitychange', onVisibility)
      release()
      const loseContext = gl.getExtension('WEBGL_lose_context')
      if (loseContext) pendingLose.set(canvas, window.setTimeout(() => loseContext.loseContext(), 0))
    }
  }, [fallback, size])

  if (fallback) {
    return (
      <div data-renderer="fallback" data-orb-state={speaking ? 'speaking' : state} style={{ width: size, height: size, flexShrink: 0 }}>
        <VisualCorePlaceholder state={speaking ? 'speaking' : state} size={size} />
      </div>
    )
  }
  const backing = Math.round(size * Math.min(window.devicePixelRatio || 1, 2))
  return (
    <canvas
      ref={canvasRef}
      role="img"
      aria-label={`Jarvis visual core — ${state}`}
      data-renderer="webgl2"
      data-orb-state={speaking ? 'speaking' : state}
      width={backing}
      height={backing}
      style={{ width: size, height: size, flexShrink: 0 }}
    />
  )
}
