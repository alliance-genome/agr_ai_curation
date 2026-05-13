import type {
  DomainEnvelopeMetadata,
  ValidationAttachmentOption,
} from '@/services/agentStudioService'
import type { ValidationAttachmentSelection } from '@/components/AgentStudio/FlowBuilder/types'

export function buildValidationAttachmentOption(
  overrides: Partial<ValidationAttachmentOption> = {}
): ValidationAttachmentOption {
  return {
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
    required: false,
    export_blocking: true,
    default_enabled: true,
    allow_opt_out: true,
    ...overrides,
  }
}

export function buildValidationAttachmentSelection(
  overrides: Partial<ValidationAttachmentSelection> = {}
): ValidationAttachmentSelection {
  return {
    ...buildValidationAttachmentOption(overrides),
    enabled: true,
    ...overrides,
  }
}

export function buildDomainEnvelopeMetadata(
  overrides: Partial<DomainEnvelopeMetadata> = {}
): DomainEnvelopeMetadata {
  const validationAttachment = buildValidationAttachmentOption()

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
        provider_refs: {
          alliance_linkml: {
            class: 'Gene',
            source_file: 'model/schema/gene.yaml',
          },
        },
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
      required: 0,
      export_blocking: 1,
      opt_out_allowed: 1,
    },
    ...overrides,
  }
}
