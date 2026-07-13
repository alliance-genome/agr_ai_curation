import { render, screen } from '@/test/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import FlowNode from './FlowNode'
import type { AgentNodeData } from './types'
import type { AgentMetadata } from '@/services/agentStudioService'
import { buildDomainEnvelopeMetadata } from '@/test/fixtures/agentStudioDomainEnvelope'

const agentMetadataMocks = vi.hoisted(() => ({
  agents: {} as Record<string, AgentMetadata>,
  isLoading: false,
  error: null,
  refresh: vi.fn(),
}))

vi.mock('reactflow', () => ({
  Handle: ({ type }: { type: string }) => <div data-testid={`handle-${type}`} />,
  Position: {
    Top: 'top',
    Bottom: 'bottom',
  },
}))

vi.mock('@/hooks/useAgentIcon', () => ({
  useAgentIcon: () => 'AI',
}))

vi.mock('@/contexts/AgentMetadataContext', () => ({
  useAgentMetadata: () => agentMetadataMocks,
}))

function buildNodeData(overrides: Partial<AgentNodeData> = {}): AgentNodeData {
  return {
    agent_id: 'gene_summary',
    agent_display_name: 'Gene Summary',
    agent_description: 'Summarize the selected gene',
    custom_instructions: 'Summarize key findings',
    output_key: 'gene_summary_output',
    ...overrides,
  }
}

describe('FlowNode', () => {
  beforeEach(() => {
    agentMetadataMocks.agents = {}
  })

  it('renders the prompt version label when prompt_version is present', () => {
    render(<FlowNode data={buildNodeData({ prompt_version: 3 })} selected={false} />)

    expect(screen.getByText('Gene Summary')).toBeInTheDocument()
    expect(screen.getByText('v3')).toBeInTheDocument()
  })

  it('does not render a prompt version label when prompt_version is missing', () => {
    render(<FlowNode data={buildNodeData()} selected={false} />)

    expect(screen.queryByText(/^v\d+$/)).not.toBeInTheDocument()
  })

  it('renders multiple formatter sources as one grouped output binding', () => {
    render(
      <FlowNode
        data={buildNodeData({
          agent_id: 'csv_formatter',
          agent_display_name: 'CSV Formatter',
          outputBinding: {
            status: 'bound',
            sources: [
              { sourceNodeId: 'genes', sourceLabel: 'Gene Extractor' },
              { sourceNodeId: 'alleles', sourceLabel: 'Allele Extractor' },
            ],
          },
        })}
        selected={false}
      />
    )

    expect(screen.getByText('2 extraction sources → output')).toBeInTheDocument()
    expect(screen.queryByText('Multiple output sources')).not.toBeInTheDocument()
  })

  it('preserves the formatter label for a single output source', () => {
    render(
      <FlowNode
        data={buildNodeData({
          agent_id: 'csv_formatter',
          agent_display_name: 'CSV Formatter',
          outputBinding: {
            status: 'bound',
            sources: [{ sourceNodeId: 'genes', sourceLabel: 'Gene Extractor' }],
            sourceNodeId: 'genes',
            sourceLabel: 'Gene Extractor',
          },
        })}
        selected={false}
      />
    )

    expect(screen.getByText('Gene Extractor → output')).toBeInTheDocument()
  })

  it('renders validation attachment state counts distinctly', () => {
    agentMetadataMocks.agents = {
      gene_summary: {
        name: 'Gene Summary',
        icon: 'AI',
        category: 'Extraction',
        domain_envelope: buildDomainEnvelopeMetadata(),
      },
    }

    render(
      <FlowNode
        data={buildNodeData({
          validation_attachments: [
            {
              attachment_id: 'active',
              domain_pack_id: 'fixture',
              validator_id: 'active',
              label: 'Active validator',
              state: 'active',
              scope: 'field',
              field_path: 'gene_symbol',
              required: true,
              blocking: true,
              default_enabled: true,
              allow_opt_out: false,
              enabled: true,
            },
            {
              attachment_id: 'under-development',
              domain_pack_id: 'fixture',
              validator_id: 'under-development',
              label: 'Gene symbol lookup',
              state: 'under_development',
              scope: 'field',
              field_path: 'gene_symbol',
              required: false,
              blocking: false,
              default_enabled: false,
              allow_opt_out: false,
              enabled: false,
            },
          ],
        })}
        selected={false}
      />
    )

    expect(screen.getByText('1 active validation')).toBeInTheDocument()
    expect(screen.getByText('1 envelope object')).toBeInTheDocument()
    expect(screen.getByText('1 blocking')).toBeInTheDocument()
    expect(screen.getByText('1 under development')).toBeInTheDocument()
  })
})
