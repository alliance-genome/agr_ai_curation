import { fireEvent, render, screen } from '@/test/test-utils'
import { describe, beforeEach, expect, it, vi } from 'vitest'

import NodeEditor from './NodeEditor'
import {
  buildDomainEnvelopeMetadata,
  buildValidationAttachmentSelection,
} from '@/test/fixtures/agentStudioDomainEnvelope'
import type { AgentNode, ValidationAttachmentSelection } from './types'

const metadataMocks = vi.hoisted(() => ({
  agents: {} as Record<string, unknown>,
}))

vi.mock('@/contexts/AgentMetadataContext', () => ({
  useAgentMetadata: () => ({
    agents: metadataMocks.agents,
    refresh: vi.fn(),
    isLoading: false,
    error: null,
  }),
}))

vi.mock('@/hooks/useAgentIcon', () => ({
  useAgentIcon: () => 'AI',
}))

const validationAttachment: ValidationAttachmentSelection = buildValidationAttachmentSelection()

function buildNode(overrides: Partial<AgentNode['data']> = {}): AgentNode {
  return {
    id: 'node_1',
    type: 'agent',
    position: { x: 0, y: 0 },
    data: {
      agent_id: 'gene_extractor',
      agent_display_name: 'Gene Extractor',
      agent_description: 'Extract gene mentions',
      custom_instructions: '',
      input_source: 'previous_output',
      output_key: 'gene_output',
      validation_attachments: [validationAttachment],
      ...overrides,
    },
  }
}

describe('NodeEditor', () => {
  beforeEach(() => {
    metadataMocks.agents = {}
  })

  it('renders envelope summary and opens the dedicated envelope inspector', () => {
    metadataMocks.agents = {
      gene_extractor: {
        name: 'Gene Extractor',
        icon: 'G',
        category: 'Extraction',
        domain_envelope: buildDomainEnvelopeMetadata(),
      },
    }
    const onViewDomainEnvelope = vi.fn()

    render(
      <NodeEditor
        node={buildNode()}
        onSave={vi.fn()}
        onClose={vi.fn()}
        availableVariables={[]}
        onViewDomainEnvelope={onViewDomainEnvelope}
      />
    )

    expect(screen.getByText('Gene Validated Reference Domain Pack')).toBeInTheDocument()
    expect(screen.getByText('1 object type')).toBeInTheDocument()
    expect(screen.getByText('1 default validator')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /view envelope details/i }))

    expect(onViewDomainEnvelope).toHaveBeenCalledWith('node_1')
  })

  it('persists allowed validation opt-outs without requiring a curator reason', () => {
    metadataMocks.agents = {
      gene_extractor: {
        name: 'Gene Extractor',
        icon: 'G',
        category: 'Extraction',
        domain_envelope: buildDomainEnvelopeMetadata(),
      },
    }
    const onSave = vi.fn()

    render(
      <NodeEditor
        node={buildNode()}
        onSave={onSave}
        onClose={vi.fn()}
        availableVariables={[]}
      />
    )

    fireEvent.click(screen.getByRole('checkbox'))
    expect(screen.queryByPlaceholderText('Reason for disabling this validator')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))

    expect(onSave).toHaveBeenCalledWith('node_1', expect.any(Object))
    const savedAttachment = onSave.mock.calls[0][1].validation_attachments[0]
    expect(savedAttachment).toEqual(expect.objectContaining({
      attachment_id: 'gene:lookup',
      enabled: false,
    }))
    expect(savedAttachment).not.toHaveProperty('opt_out_reason')
  })

  it('requires a reason only when the attachment policy explicitly asks for one', () => {
    metadataMocks.agents = {
      gene_extractor: {
        name: 'Gene Extractor',
        icon: 'G',
        category: 'Extraction',
        domain_envelope: buildDomainEnvelopeMetadata(),
      },
    }
    const onSave = vi.fn()

    render(
      <NodeEditor
        node={buildNode({
          validation_attachments: [
            buildValidationAttachmentSelection({ opt_out_reason_required: true }),
          ],
        })}
        onSave={onSave}
        onClose={vi.fn()}
        availableVariables={[]}
      />
    )

    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
    expect(onSave).not.toHaveBeenCalled()
    expect(screen.getByText('A reason is required for this opt-out.')).toBeInTheDocument()
  })

  it('separates read-only validation metadata from actionable validator checkboxes', () => {
    const actionable = buildValidationAttachmentSelection({
      attachment_id: 'disease:pending-envelope',
      label: 'Pending disease envelope validator',
      validator_binding_id: 'disease_pending_envelope_validator',
      enabled: true,
      default_enabled: true,
      allow_opt_out: true,
    })
    const planned = buildValidationAttachmentSelection({
      attachment_id: 'disease:condition-relation',
      label: 'curation db reference lookup (condition_relations[0].condition_relation_type.name)',
      validator_binding_id: 'disease_condition_relation_lookup',
      field_path: 'condition_relations[0].condition_relation_type.name',
      state: 'planned',
      enabled: false,
      default_enabled: false,
      allow_opt_out: false,
    })
    const metadataOnly = buildValidationAttachmentSelection({
      attachment_id: 'disease:required-payload-fields',
      label: 'disease required payload fields',
      validator_binding_id: undefined,
      state: 'active',
      enabled: false,
      default_enabled: false,
      allow_opt_out: false,
    })

    render(
      <NodeEditor
        node={buildNode({
          validation_attachments: [actionable, planned, metadataOnly],
        })}
        onSave={vi.fn()}
        onClose={vi.fn()}
        availableVariables={[]}
      />
    )

    expect(screen.getAllByRole('checkbox')).toHaveLength(1)
    expect(screen.getByText('Pending disease envelope validator')).toBeInTheDocument()
    expect(screen.getAllByText(/condition_relations\[0\]\.condition_relation_type\.name/i).length).toBeGreaterThan(0)
    expect(screen.getByText('disease required payload fields')).toBeInTheDocument()
    expect(screen.getByText(/not scheduled by this checkbox list/i)).toBeInTheDocument()
  })

  it('labels validation agent instructions as a steering prompt', () => {
    metadataMocks.agents = {
      gene_validator: {
        name: 'Gene Validator',
        icon: 'V',
        category: 'Validation',
        subcategory: 'Data Validation',
      },
    }

    render(
      <NodeEditor
        node={buildNode({
          agent_id: 'gene_validator',
          agent_display_name: 'Gene Validator',
          validation_attachments: undefined,
        })}
        onSave={vi.fn()}
        onClose={vi.fn()}
        availableVariables={['gene_output']}
        hasIncomingEdge
      />
    )

    expect(screen.getByText('Validation Steering Prompt (Optional)')).toBeInTheDocument()
    expect(screen.getByText(/Custom validation agents persist as regular flow steps/i)).toBeInTheDocument()
  })
})
