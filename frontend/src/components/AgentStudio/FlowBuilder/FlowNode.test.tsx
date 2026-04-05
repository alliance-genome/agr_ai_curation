import { render, screen } from '@/test/test-utils'
import { describe, expect, it, vi } from 'vitest'

import FlowNode from './FlowNode'
import type { AgentNodeData } from './types'

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

function buildNodeData(overrides: Partial<AgentNodeData> = {}): AgentNodeData {
  return {
    agent_id: 'gene_summary',
    agent_display_name: 'Gene Summary',
    agent_description: 'Summarize the selected gene',
    custom_instructions: 'Summarize key findings',
    input_source: 'previous_output',
    output_key: 'gene_summary_output',
    ...overrides,
  }
}

describe('FlowNode', () => {
  it('renders the prompt version label when prompt_version is present', () => {
    render(<FlowNode data={buildNodeData({ prompt_version: 3 })} selected={false} />)

    expect(screen.getByText('Gene Summary')).toBeInTheDocument()
    expect(screen.getByText('v3')).toBeInTheDocument()
  })

  it('does not render a prompt version label when prompt_version is missing', () => {
    render(<FlowNode data={buildNodeData()} selected={false} />)

    expect(screen.queryByText(/^v\d+$/)).not.toBeInTheDocument()
  })
})
