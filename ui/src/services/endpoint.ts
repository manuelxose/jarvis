/**
 * Reads the per-launch event endpoint handed over by `jarvis ui` as
 * `http://127.0.0.1:<port>/#token=<token>` and removes the token from the
 * address bar so it is not left in history, screenshots or copy-paste.
 *
 * Erasable TypeScript only, so Node can run this file directly (node-tests/).
 */
export interface EventEndpoint {
  readonly url: string
  readonly token: string
}

type LocationLike = Pick<Location, 'hash' | 'origin' | 'pathname' | 'search'>
type HistoryLike = Pick<History, 'replaceState'>

export function readEndpoint(
  loc: LocationLike = window.location,
  hist: HistoryLike = window.history,
): EventEndpoint | null {
  const params = new URLSearchParams(loc.hash.replace(/^#/, ''))
  if (!params.has('token')) return null
  hist.replaceState(null, '', loc.pathname + loc.search)
  const token = params.get('token') ?? ''
  if (token === '') return null
  return { url: `${loc.origin}/events`, token }
}

// Shared by the SSE hook and control client: never re-read a stripped hash.
let launchEndpoint: EventEndpoint | null | undefined
export function getLaunchEndpoint(): EventEndpoint | null {
  if (launchEndpoint === undefined) launchEndpoint = readEndpoint()
  return launchEndpoint
}
