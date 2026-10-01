import type { ControlAction } from '../protocol/commands.ts'
import { getLaunchEndpoint, type EventEndpoint } from './endpoint.ts'

/** A 202 means the daemon accepted dispatch, not that a voice turn finished. */
export async function sendControl(
  action: ControlAction,
  options: { endpoint?: EventEndpoint | null; signal?: AbortSignal; fetchImpl?: typeof fetch; timeoutMs?: number } = {},
): Promise<void> {
  const endpoint = options.endpoint === undefined ? getLaunchEndpoint() : options.endpoint
  if (!endpoint || !['activate', 'sleep'].includes(action)) throw new Error('Control is unavailable.')
  const events = new URL(endpoint.url)
  if (events.origin !== window.location.origin || events.pathname !== '/events' || events.search || events.hash) {
    throw new Error('Control endpoint is not on this page.')
  }
  const controller = new AbortController()
  const abort = () => controller.abort()
  options.signal?.addEventListener('abort', abort, { once: true })
  if (options.signal?.aborted) abort()
  const timeout = setTimeout(abort, options.timeoutMs ?? 7000)
  try {
    const response = await (options.fetchImpl ?? fetch)(new URL('/control', events).href, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Jarvis-Token': endpoint.token },
      body: JSON.stringify({ action }),
      signal: controller.signal,
      credentials: 'same-origin',
    })
    if (!response.ok) throw new Error(`Control request denied (${response.status}).`)
    if (response.status !== 202 || (await response.json() as { status?: unknown }).status !== 'accepted') {
      throw new Error('Unexpected control response.')
    }
  } catch (error) {
    if (controller.signal.aborted) throw new Error(options.signal?.aborted ? 'Control request cancelled.' : 'Control request timed out.')
    if (error instanceof TypeError) throw new Error('Control connection failed.')
    if (error instanceof SyntaxError) throw new Error('Unexpected control response.')
    throw error
  } finally {
    clearTimeout(timeout)
    options.signal?.removeEventListener('abort', abort)
  }
}
