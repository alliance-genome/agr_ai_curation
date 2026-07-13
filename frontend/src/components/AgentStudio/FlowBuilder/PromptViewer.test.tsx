import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
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
  id: 'gene:group_rules:group-alpha',
  kind: 'group_rules',
  title: 'Group alpha rules',
  content: 'GROUP ALPHA CONTENT',
  provenance: 'prompt_template:group',
  editable: true,
  locked: false,
  source_ref: 'database:gene:group-alpha',
  hash: 'group-hash',
}

const betaGroupLayer: PromptLayerInfo = {
  ...groupLayer,
  id: 'gene:group_rules:group-beta',
  title: 'Group beta rules',
  content: 'GROUP BETA CONTENT',
  source_ref: 'database:gene:group-beta',
  hash: 'beta-group-hash',
}

const agent: PromptInfo = {
  agent_id: 'gene',
  agent_name: 'Gene Agent',
  description: 'Curates genes.',
  base_prompt: 'LEGACY BASE MUST NOT BE SHOWN',
  source_file: 'database',
  has_group_rules: true,
  group_rules: {
    'group-alpha': {
      group_id: 'group-alpha',
      content: 'legacy group alpha rules',
      source_file: 'database',
    },
    'group-beta': {
      group_id: 'group-beta',
      content: 'legacy group beta rules',
      source_file: 'database',
    },
  },
  prompt_layers: layers,
  tools: ['gene_lookup'],
}

const combined: CombinedPromptResponse = {
  agent_id: 'gene',
  group_id: 'group-alpha',
  combined_prompt: [...layers, groupLayer].map((layer) => layer.content).join('\n\n'),
  effective_prompt_hash: 'combined-hash',
  layer_manifest: {
    agent_id: 'gene',
    layers: [...layers, groupLayer],
    hash: 'combined-hash',
  },
}

const betaCombined: CombinedPromptResponse = {
  agent_id: 'gene',
  group_id: 'group-beta',
  combined_prompt: [...layers, betaGroupLayer].map((layer) => layer.content).join('\n\n'),
  effective_prompt_hash: 'beta-combined-hash',
  layer_manifest: {
    agent_id: 'gene',
    layers: [...layers, betaGroupLayer],
    hash: 'beta-combined-hash',
  },
}

function createDeferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

describe('PromptViewer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    serviceMocks.fetchPromptCatalog.mockResolvedValue({
      categories: [{ category: 'Validation', agents: [agent] }],
      total_agents: 1,
      available_groups: ['group-alpha'],
      last_updated: '2026-07-13T00:00:00Z',
    })
    serviceMocks.fetchCombinedPrompt.mockResolvedValue(combined)
  })

  it('shows canonical selected-group layers with ownership and editability boundaries', async () => {
    render(<PromptViewer agentId="gene" agentName="Gene Agent" open onClose={vi.fn()} />)

    await waitFor(() => {
      expect(serviceMocks.fetchCombinedPrompt).toHaveBeenCalledWith('gene', 'group-alpha')
    })

    expect(await screen.findByRole('button', { name: 'Group alpha rules' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Generated tool guidance' }))

    expect(screen.getByText('GENERATED TOOL CONTENT')).toBeInTheDocument()
    expect(screen.getByText('Read only')).toBeInTheDocument()
    expect(screen.getByText('Locked')).toBeInTheDocument()
    expect(screen.getByText(/Owner: tool_registry/)).toBeInTheDocument()
    expect(screen.queryByText('LEGACY BASE MUST NOT BE SHOWN')).not.toBeInTheDocument()
  })

  it('renders the combined prompt in the manifest runtime order', async () => {
    render(<PromptViewer agentId="gene" agentName="Gene Agent" open onClose={vi.fn()} />)

    await screen.findByRole('button', { name: 'Group alpha rules' })
    fireEvent.click(screen.getByRole('button', { name: 'Combined' }))

    const content = screen.getByText(/LOCKED CORE CONTENT/)
    expect(content).toHaveTextContent(
      'LOCKED CORE CONTENT GENERATED TOOL CONTENT EDITABLE BASE CONTENT GROUP ALPHA CONTENT'
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

  it('keeps the latest selected group when preview requests resolve out of order', async () => {
    const alphaRequest = createDeferred<CombinedPromptResponse>()
    const betaRequest = createDeferred<CombinedPromptResponse>()
    serviceMocks.fetchCombinedPrompt.mockImplementation((_agentId: string, groupId: string) => (
      groupId === 'group-alpha' ? alphaRequest.promise : betaRequest.promise
    ))

    render(<PromptViewer agentId="gene" agentName="Gene Agent" open onClose={vi.fn()} />)

    await waitFor(() => {
      expect(serviceMocks.fetchCombinedPrompt).toHaveBeenCalledWith('gene', 'group-alpha')
    })
    fireEvent.mouseDown(screen.getByRole('combobox'))
    fireEvent.click(await screen.findByRole('option', { name: 'GROUP-BETA' }))
    await waitFor(() => {
      expect(serviceMocks.fetchCombinedPrompt).toHaveBeenCalledWith('gene', 'group-beta')
    })

    await act(async () => betaRequest.resolve(betaCombined))
    expect(await screen.findByRole('button', { name: 'Group beta rules' })).toBeInTheDocument()

    await act(async () => alphaRequest.resolve(combined))
    expect(screen.getByRole('button', { name: 'Group beta rules' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Group alpha rules' })).not.toBeInTheDocument()
  })
})
