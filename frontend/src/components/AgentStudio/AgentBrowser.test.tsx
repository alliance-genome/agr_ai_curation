import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AgentBrowser from './AgentBrowser'
import type { PromptCatalog, PromptInfo } from '@/types/promptExplorer'

const metadataMocks = vi.hoisted(() => ({
  agents: {
    restricted_agent: { allowed_group_ids: ['group_a'] },
  } as Record<string, unknown>,
}))

vi.mock('@/contexts/AgentMetadataContext', () => ({
  useAgentMetadata: () => ({
    agents: metadataMocks.agents,
    refresh: vi.fn(),
    isLoading: false,
    error: null,
  }),
}))

vi.mock('@/services/agentStudioService', () => ({
  fetchCombinedPrompt: vi.fn(),
  fetchAllTools: vi.fn().mockResolvedValue({}),
  fetchToolDetails: vi.fn(),
}))

const buildAgent = (agentId: string, name: string, overrides: Partial<PromptInfo> = {}): PromptInfo => ({
  agent_id: agentId,
  agent_name: name,
  description: `${name} description`,
  base_prompt: 'Prompt',
  source_file: 'database',
  has_group_rules: false,
  group_rules: {},
  tools: [],
  subcategory: 'Data Validation',
  ...overrides,
})

function buildCatalog(agents: PromptInfo[]): PromptCatalog {
  return {
    categories: [{ category: 'Validation', agents }],
    total_agents: agents.length,
    available_groups: [],
    last_updated: '2026-08-27T00:00:00Z',
  }
}

/** Replace ResizeObserver so a test can report a container width. */
function installResizeObserver(width: number) {
  const callbacks: ResizeObserverCallback[] = []
  class WidthReportingResizeObserver implements ResizeObserver {
    private readonly callback: ResizeObserverCallback

    constructor(callback: ResizeObserverCallback) {
      this.callback = callback
      callbacks.push(callback)
    }

    observe(target: Element): void {
      this.callback(
        [{ target, contentRect: { width } } as unknown as ResizeObserverEntry],
        this
      )
    }

    unobserve(): void {}

    disconnect(): void {}
  }
  const original = globalThis.ResizeObserver
  globalThis.ResizeObserver = WidthReportingResizeObserver as typeof ResizeObserver
  return () => {
    globalThis.ResizeObserver = original
  }
}

describe('AgentBrowser', () => {
  let restoreResizeObserver: (() => void) | null = null

  beforeEach(() => {
    metadataMocks.agents = { restricted_agent: { allowed_group_ids: ['group_a'] } }
  })

  afterEach(() => {
    restoreResizeObserver?.()
    restoreResizeObserver = null
  })

  it('shows a restriction badge for an authorized catalog entry and does not invent filtered entries', () => {
    render(
      <AgentBrowser
        catalog={buildCatalog([buildAgent('restricted_agent', 'Restricted Agent')])}
        selectedAgentId="restricted_agent"
        selectedGroupId={null}
        onAgentSelect={vi.fn()}
        onGroupSelect={vi.fn()}
      />
    )

    expect(screen.getByLabelText('Restricted Agent restricted to group_a')).toBeInTheDocument()
    expect(screen.getByText(/Available to groups: group_a/)).toBeInTheDocument()
    expect(screen.queryByText('Unauthorized Agent')).not.toBeInTheDocument()
  })

  it('renders the list pane with counts, filter tabs, search, and category groups beside the detail', () => {
    const onAgentSelect = vi.fn()
    render(
      <AgentBrowser
        catalog={buildCatalog([
          buildAgent('validator_a', 'Validator A'),
          buildAgent('ca_custom', 'Custom Agent', { subcategory: 'Shared Agents' }),
          buildAgent('extractor_b', 'Extractor B', { subcategory: 'PDF Extraction', has_group_rules: true }),
        ])}
        selectedAgentId="validator_a"
        selectedGroupId={null}
        onAgentSelect={onAgentSelect}
        onGroupSelect={vi.fn()}
      />
    )

    expect(screen.getByRole('heading', { level: 2, name: 'Agents' })).toBeInTheDocument()
    expect(screen.getByLabelText('3 agents shown')).toHaveTextContent('3')

    const filterTabs = within(screen.getByRole('tablist', { name: 'Agent list filter' })).getAllByRole('tab')
    expect(filterTabs.map((tab) => tab.textContent)).toEqual(['All (3)', 'Shared (1)', 'Templates (2)'])

    expect(screen.getByRole('textbox', { name: 'Search agents' })).toBeInTheDocument()
    expect(screen.getByText('PDF Extraction')).toBeInTheDocument()
    expect(screen.getByText('Data Validation')).toBeInTheDocument()

    // The selected agent's category is auto-expanded and the item is selected.
    const selectedItem = screen.getByRole('button', { name: /Validator A/ })
    expect(selectedItem).toHaveClass('Mui-selected')

    // Detail pane shows the selected agent next to the list.
    expect(screen.getByRole('heading', { level: 2, name: 'Validator A' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Back to Agents' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByText('PDF Extraction'))
    fireEvent.click(screen.getByRole('button', { name: /Extractor B/ }))
    expect(onAgentSelect).toHaveBeenCalledWith('extractor_b')
    expect(screen.getByText('Has group rules')).toBeInTheDocument()
  })

  it('filters by search text and shows an empty result message', () => {
    render(
      <AgentBrowser
        catalog={buildCatalog([buildAgent('validator_a', 'Validator A'), buildAgent('extractor_b', 'Extractor B')])}
        selectedAgentId={null}
        selectedGroupId={null}
        onAgentSelect={vi.fn()}
        onGroupSelect={vi.fn()}
      />
    )

    fireEvent.change(screen.getByRole('textbox', { name: 'Search agents' }), { target: { value: 'nothing here' } })
    expect(screen.getByText('No agents match: nothing here')).toBeInTheDocument()
    expect(screen.getByLabelText('0 agents shown')).toHaveTextContent('0 / 2')

    fireEvent.click(screen.getByRole('button', { name: 'Clear search' }))
    expect(screen.queryByText(/No agents match/)).not.toBeInTheDocument()
  })

  it('hides the list below the narrow threshold and returns to it with Back to Agents', () => {
    restoreResizeObserver = installResizeObserver(600)
    const onAgentSelect = vi.fn()

    render(
      <AgentBrowser
        catalog={buildCatalog([buildAgent('validator_a', 'Validator A'), buildAgent('validator_b', 'Validator B')])}
        selectedAgentId="validator_a"
        selectedGroupId={null}
        onAgentSelect={onAgentSelect}
        onGroupSelect={vi.fn()}
      />
    )

    // Detail replaces the list.
    expect(screen.getByRole('heading', { level: 2, name: 'Validator A' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { level: 2, name: 'Agents' })).not.toBeInTheDocument()

    act(() => {
      fireEvent.click(screen.getByRole('button', { name: 'Back to Agents' }))
    })
    expect(screen.getByRole('heading', { level: 2, name: 'Agents' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { level: 2, name: 'Validator A' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Validator B/ }))
    expect(onAgentSelect).toHaveBeenCalledWith('validator_b')
  })

  it('keeps both panes at desktop width', () => {
    restoreResizeObserver = installResizeObserver(1120)
    render(
      <AgentBrowser
        catalog={buildCatalog([buildAgent('validator_a', 'Validator A')])}
        selectedAgentId="validator_a"
        selectedGroupId={null}
        onAgentSelect={vi.fn()}
        onGroupSelect={vi.fn()}
      />
    )
    expect(screen.getByRole('heading', { level: 2, name: 'Agents' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Validator A' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Back to Agents' })).not.toBeInTheDocument()
  })
})
