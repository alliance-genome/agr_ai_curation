import { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@/test/test-utils'

import FlowRunProgressBar from './FlowRunProgressBar'
import type { FlowRunProgress } from './flowRunProgress'

const STARTED_AT = Date.parse('2026-10-02T12:00:00.000Z')

function progress(overrides: Partial<FlowRunProgress> = {}): FlowRunProgress {
  return {
    status: 'running',
    flowName: 'Expression flow',
    totalSteps: 5,
    startedAtMs: STARTED_AT,
    currentStep: { step: 3, name: 'Find expression' },
    ...overrides,
  }
}

describe('FlowRunProgressBar', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(STARTED_AT + 65_000)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('shows the running step, its position and the elapsed time', () => {
    render(<FlowRunProgressBar progress={progress()} />)

    expect(screen.getByText('Running "Expression flow"')).toBeInTheDocument()
    const bar = screen.getByRole('progressbar', { name: 'Flow progress' })
    expect(bar).toHaveAttribute('aria-valuenow', '40')
    expect(bar).toHaveAttribute('aria-valuetext', '2 of 5 steps finished')
    const step = screen.getByText('Step 3 of 5: Find expression')
    expect(step).toHaveAttribute('aria-live', 'polite')
    expect(screen.getByText('Elapsed: 1 min 5 s')).toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(2_000)
    })
    expect(screen.getByText('Elapsed: 1 min 7 s')).toBeInTheDocument()
  })

  it('shows that the flow is getting ready before its first step', () => {
    render(<FlowRunProgressBar progress={progress({ currentStep: null })} />)

    expect(screen.getByText('Getting ready to run 5 steps')).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: 'Flow progress' })).toHaveAttribute('aria-valuenow', '0')
  })

  it('renders nothing once the flow completes', () => {
    const { container } = render(<FlowRunProgressBar progress={progress({ status: 'completed' })} />)

    expect(container).toBeEmptyDOMElement()
  })

  it('shows which step a failed flow stopped on, without a running clock', () => {
    render(<FlowRunProgressBar progress={progress({ status: 'failed' })} />)

    expect(screen.getByTestId('flow-run-progress')).toHaveAttribute('data-status', 'failed')
    expect(screen.getByText('"Expression flow" did not finish')).toBeInTheDocument()
    expect(screen.getByText('Stopped at step 3 of 5: Find expression')).toBeInTheDocument()
    expect(screen.queryByText(/Elapsed/)).not.toBeInTheDocument()
  })

  it('explains a failure before any step started and a curator stop', () => {
    const { rerender } = render(
      <FlowRunProgressBar progress={progress({ status: 'failed', currentStep: null })} />,
    )
    expect(screen.getByText('Stopped before its first step started')).toBeInTheDocument()

    rerender(<FlowRunProgressBar progress={progress({ status: 'stopped' })} />)
    expect(screen.getByText('"Expression flow" was stopped')).toBeInTheDocument()
    expect(screen.getByText('Stopped at step 3 of 5: Find expression')).toBeInTheDocument()
  })
})
