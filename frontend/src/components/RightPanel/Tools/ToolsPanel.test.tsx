import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ToolsPanel from './ToolsPanel'

vi.mock('./ChatDefault', () => ({ default: () => <section>Chat default preference</section> }))
vi.mock('./CurationFlows', () => ({
  default: ({ onExecuteFlow, onStopFlow }: {
    onExecuteFlow: (flowId: string) => Promise<void>
    onStopFlow?: () => void | Promise<void>
  }) => (
    <section>
      Curation Flows
      <button onClick={() => void onExecuteFlow('flow-1')}>Run existing flow</button>
      <button onClick={() => void onStopFlow?.()}>Stop existing flow</button>
    </section>
  ),
}))

describe('ToolsPanel', () => {
  it('keeps Chat default separate from existing flow Run and Stop behavior', async () => {
    const onExecuteFlow = vi.fn(async () => {})
    const onStopFlow = vi.fn()
    render(
      <ToolsPanel
        sessionId="session-1"
        sseEvents={[]}
        onExecuteFlow={onExecuteFlow}
        onStopFlow={onStopFlow}
      />,
    )
    expect(screen.getByText('Chat default preference')).toBeInTheDocument()
    expect(screen.getByText('Curation Flows')).toBeInTheDocument()
    expect(screen.queryByText(/Highlight Tester/i)).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Run existing flow' }))
    await userEvent.click(screen.getByRole('button', { name: 'Stop existing flow' }))
    expect(onExecuteFlow).toHaveBeenCalledWith('flow-1')
    expect(onStopFlow).toHaveBeenCalledTimes(1)
  })
})
