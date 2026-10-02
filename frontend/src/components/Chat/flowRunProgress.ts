import type { SSEEvent } from '@/hooks/useChatStream'

export type FlowRunStatus = 'running' | 'completed' | 'failed' | 'stopped'

export interface FlowRunStep {
  step: number
  name: string
}

export interface FlowRunProgress {
  status: FlowRunStatus
  flowName: string
  totalSteps: number
  startedAtMs: number
  currentStep: FlowRunStep | null
}

function parseTimestampMs(value: unknown): number | null {
  if (typeof value !== 'string') return null
  const parsed = Date.parse(value)
  return Number.isNaN(parsed) ? null : parsed
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value > 0
}

/**
 * Derive the progress of the flow run carried by the current stream.
 *
 * Each run starts a fresh event stream, so the events hold exactly one run.
 * FLOW_STARTED gives the step count, each FLOW_STEP_STARTED moves the run to
 * that step (a retried step reports the same position again), and the run ends
 * on FLOW_FINISHED, RUN_ERROR or a user stop.
 */
export function deriveFlowRunProgress(
  events: SSEEvent[],
  sessionId: string | null | undefined,
): FlowRunProgress | null {
  let progress: FlowRunProgress | null = null

  for (const event of events) {
    if (sessionId && event.session_id && event.session_id !== sessionId) continue

    if (event.type === 'FLOW_STARTED') {
      const startedAtMs = parseTimestampMs(event.timestamp)
      if (startedAtMs === null || !isPositiveInteger(event.total_steps)) continue
      progress = {
        status: 'running',
        flowName: typeof event.flow_name === 'string' ? event.flow_name : '',
        totalSteps: event.total_steps,
        startedAtMs,
        currentStep: null,
      }
      continue
    }

    if (!progress || progress.status !== 'running') continue

    if (event.type === 'FLOW_STEP_STARTED') {
      if (!isPositiveInteger(event.step) || typeof event.step_name !== 'string') continue
      progress = {
        ...progress,
        currentStep: { step: event.step, name: event.step_name },
      }
      continue
    }

    if (event.type === 'FLOW_FINISHED') {
      progress = { ...progress, status: event.status === 'completed' ? 'completed' : 'failed' }
    } else if (event.type === 'RUN_ERROR') {
      progress = {
        ...progress,
        status: event.error_type === 'FlowCancelled' ? 'stopped' : 'failed',
      }
    } else if (event.type === 'STOP_CONFIRMED') {
      progress = { ...progress, status: 'stopped' }
    }
  }

  return progress
}

/** Format a duration as plain words, e.g. "45 s", "2 min 5 s", "1 h 3 min". */
export function formatElapsed(elapsedMs: number): string {
  const totalSeconds = Math.max(0, Math.floor(elapsedMs / 1000))
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60

  if (hours > 0) return `${hours} h ${minutes} min`
  if (minutes > 0) return `${minutes} min ${seconds} s`
  return `${seconds} s`
}

/** Curator-facing position text, e.g. "Step 3 of 5: Find genes". */
export function describeFlowStep(progress: FlowRunProgress): string {
  if (!progress.currentStep) {
    return `Getting ready to run ${progress.totalSteps} ${progress.totalSteps === 1 ? 'step' : 'steps'}`
  }
  return `Step ${progress.currentStep.step} of ${progress.totalSteps}: ${progress.currentStep.name}`
}
