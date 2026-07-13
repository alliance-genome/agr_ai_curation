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
      />
    )

    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))

    expect(onSave).toHaveBeenCalledWith('node_1', expect.any(Object))
    const savedAttachment = onSave.mock.calls[0][1].validation_attachments[0]
    expect(savedAttachment).toEqual(expect.objectContaining({
      attachment_id: 'gene:lookup',
      enabled: false,
    }))
    expect(savedAttachment).not.toHaveProperty('export_blocking')
  })

  it('shows skipped state immediately when an automatic validator is opted out', () => {
    render(
      <NodeEditor
        node={buildNode({
          validation_groups: [
            {
              group_id: 'gene:lookup',
              state: 'automatic',
              binding_id: validationAttachment.validator_binding_id,
              attachment_id: validationAttachment.attachment_id,
              label: validationAttachment.label,
              required: validationAttachment.required,
              blocking: validationAttachment.blocking,
              allow_opt_out: validationAttachment.allow_opt_out,
            },
          ],
        })}
        onSave={vi.fn()}
        onClose={vi.fn()}
      />
    )

    expect(screen.getByText('automatic')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('checkbox'))

    expect(screen.getByText('skipped')).toBeInTheDocument()
    expect(screen.queryByText('automatic')).not.toBeInTheDocument()
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
    const futureBinding = buildValidationAttachmentSelection({
      attachment_id: 'disease:condition-relation',
      label: 'Condition relation type lookup',
      target_label: 'Disease annotation Condition relation type',
      validator_binding_id: 'disease_condition_relation_lookup',
      field_path: 'condition_relations[0].condition_relation_type.name',
      state: 'under_development',
      state_explanation: 'Condition relation dispatch is being wired in the domain pack.',
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
    const underDevelopment = buildValidationAttachmentSelection({
      attachment_id: 'disease:ontology-term',
      label: 'Disease ontology term lookup',
      target_label: 'Disease annotation Disease term',
      validator_binding_id: 'disease_ontology_term_lookup',
      field_path: 'disease_annotation_object.curie',
      state: 'under_development',
      state_explanation: 'Ontology dispatch is being wired in the domain pack.',
      enabled: false,
      default_enabled: false,
      allow_opt_out: false,
    })

    render(
      <NodeEditor
        node={buildNode({
          validation_attachments: [actionable, futureBinding, metadataOnly, underDevelopment],
        })}
        onSave={vi.fn()}
        onClose={vi.fn()}
      />
    )

    expect(screen.getAllByRole('checkbox')).toHaveLength(1)
    expect(screen.getByText('Pending disease envelope validator')).toBeInTheDocument()
    expect(screen.getByText('Condition relation type lookup')).toBeInTheDocument()
    expect(screen.getByText('Disease annotation Condition relation type')).toBeInTheDocument()
    expect(screen.getByText('Condition relation dispatch is being wired in the domain pack.')).toBeInTheDocument()
    expect(screen.queryByText(/condition_relations\[0\]\.condition_relation_type\.name/i)).not.toBeInTheDocument()
    expect(screen.getByText('disease required payload fields')).toBeInTheDocument()
    expect(screen.getByText('Disease ontology term lookup')).toBeInTheDocument()
    expect(screen.getAllByText('under development')).toHaveLength(2)
    expect(screen.getByText('Ontology dispatch is being wired in the domain pack.')).toBeInTheDocument()
    expect(screen.getByText(/Under-development and metadata-only validators are not scheduled by this checkbox list/i)).toBeInTheDocument()
  })

  it('shows custom replacement status for validation groups', () => {
    const onSave = vi.fn()
    const replaced = buildValidationAttachmentSelection({
      attachment_id: 'gene:lookup',
      validator_binding_id: 'gene_lookup',
      validator_package_id: 'agr.alliance',
      validator_agent_id: 'gene_validation',
      label: 'Gene lookup',
      blocking: true,
      enabled: true,
    })

    render(
      <NodeEditor
        node={buildNode({
          validation_attachments: [replaced],
          validation_groups: [
            {
              group_id: 'gene:lookup',
              state: 'replaced',
              binding_id: 'gene_lookup',
              attachment_id: 'gene:lookup',
              validator_node_id: 'node_2',
              label: 'Gene lookup',
              required: false,
              blocking: true,
              allow_opt_out: true,
            },
          ],
        })}
        onSave={onSave}
        onClose={vi.fn()}
      />
    )

    expect(screen.getByText('custom replacement')).toBeInTheDocument()
    expect(screen.getByText('blocking')).toBeInTheDocument()
    expect(screen.getByText('gene v0.1.0 / agr.alliance:gene_validation')).toBeInTheDocument()
    expect(screen.getByRole('checkbox')).toBeDisabled()

    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))

    expect(onSave).toHaveBeenCalledWith('node_1', expect.any(Object))
    expect(onSave.mock.calls[0][1].validation_attachments[0]).toEqual(expect.objectContaining({
      attachment_id: 'gene:lookup',
      enabled: true,
    }))
  })

  it('shows supplemental status for validation groups', () => {
    const supplemental = buildValidationAttachmentSelection({
      attachment_id: 'gene:supplemental-lookup',
      validator_binding_id: 'gene_supplemental_lookup',
      label: 'Supplemental gene lookup',
      enabled: true,
    })

    render(
      <NodeEditor
        node={buildNode({
          validation_attachments: [supplemental],
          validation_groups: [
            {
              group_id: 'gene:supplemental-lookup',
              state: 'supplemental',
              binding_id: 'gene_supplemental_lookup',
              attachment_id: 'gene:supplemental-lookup',
              label: 'Supplemental gene lookup',
              required: false,
              blocking: false,
              allow_opt_out: true,
            },
          ],
        })}
        onSave={vi.fn()}
        onClose={vi.fn()}
      />
    )

    expect(screen.getByText('Supplemental gene lookup')).toBeInTheDocument()
    expect(screen.getByText('supplemental')).toBeInTheDocument()
  })

  it('shows supplemental validation groups without declared attachment metadata', () => {
    render(
      <NodeEditor
        node={buildNode({
          validation_attachments: [],
          validation_groups: [
            {
              group_id: 'edge:validation_3',
              state: 'supplemental',
              binding_id: 'curator_extra_lookup',
              attachment_id: null,
              edge_id: 'validation_3',
              validator_node_id: 'node_4',
              label: null,
              required: false,
              blocking: false,
              allow_opt_out: false,
            },
          ],
        })}
        onSave={vi.fn()}
        onClose={vi.fn()}
      />
    )

    expect(screen.queryAllByRole('checkbox')).toHaveLength(0)
    expect(screen.getByText('supplemental')).toBeInTheDocument()
    expect(screen.getByText('Supplemental validator: curator_extra_lookup')).toBeInTheDocument()
    expect(screen.getByText('Binding curator_extra_lookup')).toBeInTheDocument()
    expect(screen.getByText('validator node node_4 / edge validation_3')).toBeInTheDocument()
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
      />
    )

    expect(screen.getByText('Validation Steering Prompt (Optional)')).toBeInTheDocument()
    expect(screen.getByText(/Custom validation agents attach to extraction steps/i)).toBeInTheDocument()
  })

  it('describes multiple formatter sources as one grouped input', () => {
    metadataMocks.agents = {
      csv_formatter: {
        name: 'CSV Formatter',
        icon: 'CSV',
        category: 'Output',
        subcategory: 'Formatter',
      },
    }

    render(
      <NodeEditor
        node={buildNode({
          agent_id: 'csv_formatter',
          agent_display_name: 'CSV Formatter',
          validation_attachments: undefined,
        })}
        outputBinding={{
          status: 'bound',
          sources: [
            { sourceNodeId: 'genes', sourceLabel: 'Gene Extractor' },
            { sourceNodeId: 'go_validation', sourceLabel: 'GO Validator' },
          ],
        }}
        onSave={vi.fn()}
        onClose={vi.fn()}
      />
    )

    expect(screen.getByText(/Configuring one output from/i)).toBeInTheDocument()
    expect(screen.getByText('2 source steps')).toBeInTheDocument()
    expect(screen.getByText(/Gene Extractor, GO Validator/i)).toBeInTheDocument()
    expect(screen.getByText(/one grouped input/i)).toBeInTheDocument()
  })
})
