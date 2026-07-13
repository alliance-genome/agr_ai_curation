import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import PromptViewer from './PromptViewer'
import type { CombinedPromptResponse, PromptInfo, PromptLayerInfo } from '@/types/promptExplorer'

const serviceMocks = vi.hoisted(() => ({
  fetchPromptCatalog: vi.fn(),
  fetchCombinedPrompt: vi.fn(),
}))

vi.mock('@/services/agentStudioService', () => serviceMocks)

const layers: PromptLayerInfo[] = [
  {
    id: 'gene:core_static',
    kind: 'core_static',
    title: 'Platform core contract',
    content: 'LOCKED CORE CONTENT',
    provenance: 'backend_static',
    editable: false,
    locked: true,
    source_ref: 'core:gene',
    hash: 'core-hash',
  },
  {
    id: 'gene:core_generated',
    kind: 'core_generated',
    title: 'Generated tool guidance',
    content: 'GENERATED TOOL CONTENT',
    provenance: 'tool_registry',
    editable: false,
    locked: true,
    source_ref: 'registry:gene',
    hash: 'generated-hash',
  },
  {
    id: 'gene:base_prompt',
    kind: 'base_prompt',
    title: 'Editable base prompt',
    content: 'EDITABLE BASE CONTENT',
    provenance: 'prompt_template:system',
    editable: true,
    locked: false,
    source_ref: 'database:gene',
    hash: 'base-hash',
  },
]

const groupLayer: PromptLayerInfo = {
  id: 'gene:group_rules:WB',
  kind: 'group_rules',
  title: 'WB group rules',
  content: 'WB GROUP CONTENT',
  provenance: 'prompt_template:group',
  editable: true,
  locked: false,
  source_ref: 'database:gene:WB',
  hash: 'group-hash',
}

const agent: PromptInfo = {
  agent_id: 'gene',
  agent_name: 'Gene Agent',
  description: 'Curates genes.',
  base_prompt: 'LEGACY BASE MUST NOT BE SHOWN',
  source_file: 'database',
  has_group_rules: true,
  group_rules: {
    WB: { group_id: 'WB', content: 'legacy WB rules', source_file: 'database' },
  },
  prompt_layers: layers,
  tools: ['gene_lookup'],
}

const combined: CombinedPromptResponse = {
  agent_id: 'gene',
  group_id: 'WB',
  combined_prompt: [...layers, groupLayer].map((layer) => layer.content).join('\n\n'),
  effective_prompt_hash: 'combined-hash',
  layer_manifest: {
    agent_id: 'gene',
    layers: [...layers, groupLayer],
    hash: 'combined-hash',
  },
}

describe('PromptViewer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    serviceMocks.fetchPromptCatalog.mockResolvedValue({
      categories: [{ category: 'Validation', agents: [agent] }],
      total_agents: 1,
      available_groups: ['WB'],
      last_updated: '2026-07-13T00:00:00Z',
    })
    serviceMocks.fetchCombinedPrompt.mockResolvedValue(combined)
  })

  it('shows canonical selected-group layers with ownership and editability boundaries', async () => {
    render(<PromptViewer agentId="gene" agentName="Gene Agent" open onClose={vi.fn()} />)

    await waitFor(() => {
      expect(serviceMocks.fetchCombinedPrompt).toHaveBeenCalledWith('gene', 'WB')
    })

    expect(await screen.findByRole('button', { name: 'WB group rules' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Generated tool guidance' }))

    expect(screen.getByText('GENERATED TOOL CONTENT')).toBeInTheDocument()
    expect(screen.getByText('Read only')).toBeInTheDocument()
    expect(screen.getByText('Locked')).toBeInTheDocument()
    expect(screen.getByText(/Owner: tool_registry/)).toBeInTheDocument()
    expect(screen.queryByText('LEGACY BASE MUST NOT BE SHOWN')).not.toBeInTheDocument()
  })

  it('renders the combined prompt in the manifest runtime order', async () => {
    render(<PromptViewer agentId="gene" agentName="Gene Agent" open onClose={vi.fn()} />)

    await screen.findByRole('button', { name: 'WB group rules' })
    fireEvent.click(screen.getByRole('button', { name: 'Combined' }))

    const content = screen.getByText(/LOCKED CORE CONTENT/)
    expect(content).toHaveTextContent(
      'LOCKED CORE CONTENT GENERATED TOOL CONTENT EDITABLE BASE CONTENT WB GROUP CONTENT'
    )
    expect(screen.getByText('4 layers shown in runtime order')).toBeInTheDocument()
  })

  it('surfaces a selected-group combined prompt failure without rendering partial content', async () => {
    serviceMocks.fetchCombinedPrompt.mockRejectedValue(new Error('request failed'))

    render(<PromptViewer agentId="gene" agentName="Gene Agent" open onClose={vi.fn()} />)

    expect(await screen.findByText('Failed to load the selected group prompt layers.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Combined' }))

    expect(screen.queryByText(/LOCKED CORE CONTENT/)).not.toBeInTheDocument()
    expect(screen.queryByText(/EDITABLE BASE CONTENT/)).not.toBeInTheDocument()
    expect(screen.queryByText(/layers shown in runtime order/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Copy prompt' })).toBeDisabled()
  })
})
