import { describe, expect, it } from 'vitest'

import type { SSEEvent } from '@/hooks/useChatStream'

import { deriveFlowRunProgress, describeFlowStep, formatElapsed } from './flowRunProgress'

const flowStarted: SSEEvent = {
  type: 'FLOW_STARTED',
  session_id: 'session-1',
  timestamp: '2026-10-02T12:00:00.000Z',
  flow_name: 'Expression flow',
  total_steps: 3,
}

function stepStarted(step: number, stepName: string): SSEEvent {
  return {
    type: 'FLOW_STEP_STARTED',
    session_id: 'session-1',
    timestamp: '2026-10-02T12:00:05.000Z',
    step,
    total_steps: 3,
    step_name: stepName,
  }
}

describe('deriveFlowRunProgress', () => {
  it('is empty for a run that is not a flow', () => {
    expect(deriveFlowRunProgress([{ type: 'RUN_STARTED' }], 'session-1')).toBeNull()
  })

  it('reports the flow as getting ready until its first step starts', () => {
    const progress = deriveFlowRunProgress([flowStarted], 'session-1')

    expect(progress).toEqual({
      status: 'running',
      flowName: 'Expression flow',
      totalSteps: 3,
      startedAtMs: Date.parse('2026-10-02T12:00:00.000Z'),
      currentStep: null,
    })
    expect(describeFlowStep(progress!)).toBe('Getting ready to run 3 steps')
  })

  it('follows the most recently started step, including a retried step', () => {
    const progress = deriveFlowRunProgress([
      flowStarted,
      stepStarted(1, 'Find genes'),
      stepStarted(2, 'Find expression'),
      stepStarted(2, 'Find expression'),
    ], 'session-1')

    expect(progress?.currentStep).toEqual({ step: 2, name: 'Find expression' })
    expect(describeFlowStep(progress!)).toBe('Step 2 of 3: Find expression')
  })

  it('completes on a completed FLOW_FINISHED', () => {
    const progress = deriveFlowRunProgress([
      flowStarted,
      stepStarted(3, 'Write the table'),
      { type: 'FLOW_FINISHED', status: 'completed' },
    ], 'session-1')

    expect(progress?.status).toBe('completed')
  })

  it('keeps the step a failed run stopped on', () => {
    const finishedFailed = deriveFlowRunProgress([
      flowStarted,
      stepStarted(2, 'Find expression'),
      { type: 'FLOW_FINISHED', status: 'failed' },
    ], 'session-1')
    const runError = deriveFlowRunProgress([
      flowStarted,
      stepStarted(2, 'Find expression'),
      { type: 'RUN_ERROR', message: 'Flow execution failed unexpectedly.' },
      stepStarted(3, 'Late event'),
    ], 'session-1')

    expect(finishedFailed).toMatchObject({
      status: 'failed',
      currentStep: { step: 2, name: 'Find expression' },
    })
    expect(runError).toMatchObject({
      status: 'failed',
      currentStep: { step: 2, name: 'Find expression' },
    })
  })

  it('marks a run the curator stopped as stopped', () => {
    expect(deriveFlowRunProgress([
      flowStarted,
      { type: 'RUN_ERROR', error_type: 'FlowCancelled' },
    ], 'session-1')?.status).toBe('stopped')
    expect(deriveFlowRunProgress([
      flowStarted,
      { type: 'STOP_CONFIRMED', session_id: 'session-1' },
    ], 'session-1')?.status).toBe('stopped')
  })

  it('ignores events from another session and malformed step events', () => {
    const progress = deriveFlowRunProgress([
      flowStarted,
      { ...stepStarted(1, 'Other session'), session_id: 'session-2' },
      { type: 'FLOW_STEP_STARTED', step: 'two', step_name: 'Bad step' },
    ], 'session-1')

    expect(progress?.currentStep).toBeNull()
  })
})

describe('formatElapsed', () => {
  it('formats elapsed time in plain units', () => {
    expect(formatElapsed(-500)).toBe('0 s')
    expect(formatElapsed(45_400)).toBe('45 s')
    expect(formatElapsed(125_000)).toBe('2 min 5 s')
    expect(formatElapsed(3_780_000)).toBe('1 h 3 min')
  })
})
