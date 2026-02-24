import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AgentStudioPage from './AgentStudioPage'

const serviceMocks = vi.hoisted(() => ({
  fetchPromptCatalog: vi.fn(),
  cloneAgentToWorkshop: vi.fn(),
}))

vi.mock('@/services/agentStudioService', () => serviceMocks)

vi.mock('@/components/AgentStudio/OpusChat', () => ({
  default: ({
    onApplyWorkshopPromptUpdate,
  }: {
    onApplyWorkshopPromptUpdate?: (proposal: { prompt: string; summary?: string; apply_mode?: 'replace' | 'targeted_edit' }) => void
  }) => (
    <div data-testid="opus-chat">
      Opus
      <button
        onClick={() =>
          onApplyWorkshopPromptUpdate?.({
            prompt: 'Prompt from Opus',
            summary: 'Updated from chat',
            apply_mode: 'replace',
          })
        }
      >
        apply-workshop-update
      </button>
    </div>
  ),
}))

vi.mock('@/components/AgentStudio/FlowBuilder', () => ({
  FlowBuilder: () => <div data-testid="flow-builder">Flow</div>,
}))

vi.mock('@/components/AgentStudio/AgentBrowser', () => ({
  default: ({ onCloneToWorkshop }: { onCloneToWorkshop: (agentId: string) => void }) => (
    <button onClick={() => onCloneToWorkshop('ca_source')}>clone-custom</button>
  ),
}))

vi.mock('@/components/AgentStudio/PromptWorkshop/PromptWorkshop', () => ({
  default: ({
    initialCustomAgentId,
    initialParentAgentId,
    incomingPromptUpdate,
  }: {
    initialCustomAgentId?: string | null
    initialParentAgentId?: string | null
    incomingPromptUpdate?: { prompt?: string } | null
  }) => (
    <div data-testid="prompt-workshop">
      custom:{initialCustomAgentId || 'none'} parent:{initialParentAgentId || 'none'} incoming:{incomingPromptUpdate?.prompt || 'none'}
    </div>
  ),
}))

describe('AgentStudioPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('passes cloned custom agent id into PromptWorkshop after clone-to-workshop', async () => {
    serviceMocks.fetchPromptCatalog.mockResolvedValue({
      categories: [],
      total_agents: 0,
      available_mods: [],
      last_updated: '2026-02-23T00:00:00Z',
    })
    serviceMocks.cloneAgentToWorkshop.mockResolvedValue({
      id: '11111111-1111-1111-1111-111111111111',
      template_source: 'gene',
    })

    render(
      <MemoryRouter>
        <AgentStudioPage />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(serviceMocks.fetchPromptCatalog).toHaveBeenCalledTimes(1)
    })

    fireEvent.click(screen.getByText('clone-custom'))

    await waitFor(() => {
      expect(serviceMocks.cloneAgentToWorkshop).toHaveBeenCalledWith('ca_source')
    })

    await waitFor(() => {
      expect(screen.getByTestId('prompt-workshop')).toHaveTextContent(
        'custom:11111111-1111-1111-1111-111111111111 parent:gene incoming:none'
      )
    })
  })

  it('routes approved Opus workshop prompt updates to PromptWorkshop', async () => {
    serviceMocks.fetchPromptCatalog.mockResolvedValue({
      categories: [],
      total_agents: 0,
      available_mods: [],
      last_updated: '2026-02-23T00:00:00Z',
    })
    serviceMocks.cloneAgentToWorkshop.mockResolvedValue({
      id: '11111111-1111-1111-1111-111111111111',
      template_source: 'gene',
    })

    render(
      <MemoryRouter>
        <AgentStudioPage />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(serviceMocks.fetchPromptCatalog).toHaveBeenCalledTimes(1)
    })

    fireEvent.click(screen.getByText('clone-custom'))

    await waitFor(() => {
      expect(screen.getByTestId('prompt-workshop')).toHaveTextContent('incoming:none')
    })

    fireEvent.click(screen.getByText('apply-workshop-update'))

    await waitFor(() => {
      expect(screen.getByTestId('prompt-workshop')).toHaveTextContent('incoming:Prompt from Opus')
    })
  })
})
