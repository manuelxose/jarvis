/** The only daemon actions exposed by the command center. */
export type ControlAction = 'activate' | 'sleep'

export const CONTROL_LABELS: Record<ControlAction, { title: string; consequence: string }> = {
  activate: { title: 'Activate Jarvis', consequence: 'Request a new voice activation. Jarvis may begin listening.' },
  sleep: { title: 'Put Jarvis to sleep', consequence: 'Stop the current active voice session.' },
}
