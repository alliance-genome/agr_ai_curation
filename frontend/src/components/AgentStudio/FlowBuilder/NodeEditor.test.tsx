import { fireEvent, render, screen } from '@/test/test-utils'
import { describe, beforeEach, expect, it, vi } from 'vitest'

import NodeEditor from './NodeEditor'
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

const validationAttachment: ValidationAttachmentSelection = {
  attachment_id: 'gene:lookup',
  domain_pack_id: 'gene',
  domain_pack_version: '0.1.0',
  validator_id: 'gene_lookup',
  validator_binding_id: 'gene_lookup',
  validation_kind: 'db_backed_reference_lookup',
  state: 'active',
  scope: 'field',
  object_type: 'gene_mention_evidence',
  field_path: 'gene_symbol',
  label: 'Gene lookup',
  required: true,
  export_blocking: true,
  default_enabled: true,
  allow_opt_out: true,
  opt_out_reason_required: true,
  enabled: true,
}

function buildDomainEnvelopeMetadata() {
  return {
    domain_pack_id: 'gene',
    domain_pack_version: '0.1.0',
    display_name: 'Gene Validated Reference Domain Pack',
    description: 'Envelope metadata for gene mentions.',
    status: 'in_development',
    metadata_api_version: '1.0.0',
    schema_refs: [
      {
        schema_id: 'alliance.linkml.Gene',
        provider: 'alliance_linkml',
        name: 'Gene',
        version: 'abc123',
      },
    ],
    provider_refs: {
      alliance_linkml: {
        schema_ref: 'alliance.linkml',
        source_file: 'model/schema/gene.yaml',
      },
    },
    semantic_source_note: 'Domain envelope objects are the semantic source of truth; review rows are projections.',
    source_of_truth_notes: [
      'Domain envelope objects are the semantic source of truth; review rows are projections.',
      'Gene mention evidence / Gene symbol: source of truth is alliance_linkml.',
    ],
    validation_attachments: [validationAttachment],
    model_definitions: [],
    object_definitions: [
      {
        object_type: 'gene_mention_evidence',
        display_name: 'Gene mention evidence',
        description: 'A verified paper gene mention.',
        object_role: 'validated_reference',
        model_ref: 'GeneMentionEvidencePayload',
        schema_ref: {
          schema_id: 'alliance.linkml.Gene',
          provider: 'alliance_linkml',
          name: 'Gene',
          version: 'abc123',
        },
        definition_state: 'stable',
        definition_notes: [],
        provider_refs: {},
        validation_attachments: [],
        fields: [
          {
            field_path: 'gene_symbol',
            display_name: 'Gene symbol',
            description: 'Current accepted symbol for the resolved gene.',
            field_type: 'string',
            required: true,
            definition_state: 'stable',
            definition_notes: [],
            provider_refs: {
              alliance_linkml: {
                slot: 'gene_symbol',
                range: 'GeneSymbolSlotAnnotation',
              },
            },
            source_of_truth: 'alliance_linkml',
            validation_policy: null,
            validation_attachments: [validationAttachment],
          },
        ],
      },
    ],
    validation_summary: {
      total: 1,
      by_state: { active: 1, planned: 0, blocked: 0 },
      by_scope: { pack: 0, object: 0, field: 1 },
      default_enabled: 1,
      required: 1,
      export_blocking: 1,
      opt_out_allowed: 1,
    },
  }
}

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

  it('renders envelope metadata and automatic validation guidance for extraction nodes', () => {
    metadataMocks.agents = {
      gene_extractor: {
        name: 'Gene Extractor',
        icon: 'G',
        category: 'Extraction',
        domain_envelope: buildDomainEnvelopeMetadata(),
      },
    }

    render(
      <NodeEditor
        node={buildNode()}
        onSave={vi.fn()}
        onClose={vi.fn()}
        availableVariables={[]}
      />
    )

    expect(screen.getByTestId('domain-envelope-metadata-panel')).toBeInTheDocument()
    expect(screen.getByText('Gene Validated Reference Domain Pack')).toBeInTheDocument()
    expect(screen.getByText(/semantic source of truth/i)).toBeInTheDocument()
    expect(screen.getByText('Gene mention evidence')).toBeInTheDocument()
    expect(screen.getByText('gene_symbol')).toBeInTheDocument()
    expect(screen.getByText(/Active default validators run automatically/i)).toBeInTheDocument()
  })

  it('persists allowed validation opt-outs with a curator reason', () => {
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
    fireEvent.change(screen.getByPlaceholderText('Reason for disabling this validator'), {
      target: { value: 'Manual curator lookup is required for this paper.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))

    expect(onSave).toHaveBeenCalledWith(
      'node_1',
      expect.objectContaining({
        validation_attachments: [
          expect.objectContaining({
            attachment_id: 'gene:lookup',
            enabled: false,
            opt_out_reason: 'Manual curator lookup is required for this paper.',
          }),
        ],
      })
    )
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
