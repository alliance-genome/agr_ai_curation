import { render, screen, within } from '@/test/test-utils'
import { describe, expect, it } from 'vitest'

import DomainEnvelopeMetadataPanel from './DomainEnvelopeMetadataPanel'
import {
  buildDomainEnvelopeMetadata,
  buildValidationAttachmentOption,
} from '@/test/fixtures/agentStudioDomainEnvelope'

describe('DomainEnvelopeMetadataPanel', () => {
  it('makes object-level active validators visible in the object detail list', () => {
    const objectLevelActive = buildValidationAttachmentOption({
      attachment_id: 'disease:object-active',
      domain_pack_id: 'agr.alliance.disease',
      validator_id: 'disease_annotation_lookup',
      state: 'active',
      scope: 'object',
      object_type: 'DiseaseAnnotation',
      field_path: undefined,
      label: 'Disease annotation lookup',
      target_label: 'Disease annotation',
      description: 'Checks the extracted disease assertion before review.',
      default_enabled: true,
      blocking: false,
    })
    const fieldPlanned = buildValidationAttachmentOption({
      attachment_id: 'disease:term-name-planned',
      domain_pack_id: 'agr.alliance.disease',
      validator_id: 'disease_term_name_lookup',
      state: 'planned',
      scope: 'field',
      object_type: 'DiseaseAnnotation',
      field_path: 'disease_annotation_object.name',
      label: 'Disease term name lookup',
      target_label: 'Disease annotation Disease term name',
      default_enabled: false,
      blocking: false,
    })
    const metadata = buildDomainEnvelopeMetadata({
      domain_pack_id: 'agr.alliance.disease',
      display_name: 'Alliance Disease Domain Pack',
      validation_attachments: [objectLevelActive, fieldPlanned],
      object_definitions: [
        {
          object_type: 'DiseaseAnnotation',
          display_name: 'Disease annotation',
          description: 'Pending disease assertion.',
          object_role: 'curatable_unit',
          model_ref: 'DiseaseAnnotation',
          schema_ref: null,
          definition_state: 'stable',
          definition_notes: [],
          provider_refs: {},
          validation_attachments: [objectLevelActive],
          fields: [
            {
              field_path: 'disease_annotation_object.name',
              display_name: 'Disease term name',
              description: 'Disease Ontology label for the asserted disease term.',
              field_type: 'string',
              required: true,
              definition_state: 'stable',
              definition_notes: [],
              provider_refs: {},
              source_of_truth: 'alliance_linkml',
              validation_policy: null,
              validation_attachments: [fieldPlanned],
            },
          ],
        },
      ],
      validation_summary: {
        total: 2,
        by_state: { active: 1, planned: 1, blocked: 0, under_development: 0 },
        by_scope: { pack: 0, object: 1, field: 1 },
        default_enabled: 1,
        required: 0,
        blocking: 0,
        opt_out_allowed: 1,
      },
    })

    render(<DomainEnvelopeMetadataPanel metadata={metadata} layout="flow-editor" compact />)

    const objectSection = screen.getByText('Object validation').closest('.MuiAccordion-root')
    expect(objectSection).not.toBeNull()
    expect(within(objectSection as HTMLElement).getByText('Object validation')).toBeInTheDocument()
    expect(within(objectSection as HTMLElement).getByText('Disease annotation lookup')).toBeInTheDocument()
    expect(within(objectSection as HTMLElement).getAllByText('active').length).toBeGreaterThan(0)
    expect(within(objectSection as HTMLElement).getAllByText('Disease annotation').length).toBeGreaterThan(0)
    expect(screen.queryByText('auto')).not.toBeInTheDocument()
  })

  it('groups active and under-development validator capabilities with owners', () => {
    const active = buildValidationAttachmentOption({
      attachment_id: 'gene:active',
      validator_id: 'gene_lookup',
      validator_binding_id: 'gene_lookup',
      validator_package_id: 'agr.alliance',
      validator_agent_id: 'gene_validation',
      label: 'Gene lookup',
      target_label: 'Gene symbol',
      state: 'active',
      required: true,
      blocking: true,
      allow_opt_out: true,
    })
    const underDevelopment = buildValidationAttachmentOption({
      attachment_id: 'gene:future',
      validator_id: 'gene_expression_lookup',
      validator_binding_id: 'gene_expression_lookup',
      validator_package_id: 'agr.alliance',
      validator_agent_id: 'gene_validation',
      label: 'Gene expression lookup',
      target_label: 'Gene expression term',
      state: 'under_development',
      state_explanation: 'Expression validation needs ontology wiring.',
      default_enabled: false,
      blocking: false,
      allow_opt_out: false,
    })
    const metadata = buildDomainEnvelopeMetadata({
      validation_attachments: [active, underDevelopment],
      validation_summary: {
        total: 2,
        by_state: { active: 1, planned: 0, blocked: 0, under_development: 1 },
        by_scope: { pack: 0, object: 0, field: 2 },
        default_enabled: 1,
        required: 1,
        blocking: 1,
        opt_out_allowed: 1,
      },
    })

    render(<DomainEnvelopeMetadataPanel metadata={metadata} />)

    expect(screen.getByText('Validator capabilities')).toBeInTheDocument()
    expect(screen.getByText('Gene lookup')).toBeInTheDocument()
    expect(screen.getByText('Gene expression lookup')).toBeInTheDocument()
    expect(screen.getAllByText('agr.alliance:gene_validation').length).toBeGreaterThan(0)
    expect(screen.getByText('Expression validation needs ontology wiring.')).toBeInTheDocument()
    expect(screen.getByText(/not runnable and do not create replacement obligations/i)).toBeInTheDocument()
  })
})
