import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import EnvelopeTab from './EnvelopeTab'
import {
  buildDomainEnvelopeMetadata,
  buildValidationAttachmentOption,
} from '@/test/fixtures/agentStudioDomainEnvelope'

function buildDiseaseLikeMetadata() {
  const base = buildDomainEnvelopeMetadata()
  const baseField = base.object_definitions[0].fields[0]
  const termLookup = buildValidationAttachmentOption({
    attachment_id: 'term',
    validator_id: 'term_lookup',
    label: 'Term lookup',
    blocking: true,
    allow_opt_out: false,
    description: 'Resolves the term name against the ontology.',
  })
  const subjectCheck = buildValidationAttachmentOption({
    attachment_id: 'subject',
    validator_id: 'subject_materialization',
    label: 'Subject materialization',
    blocking: false,
    allow_opt_out: true,
    field_path: 'subject.identifier',
  })
  const referenceFuture = buildValidationAttachmentOption({
    attachment_id: 'reference',
    validator_id: 'reference_materialization',
    label: 'Reference materialization',
    state: 'under_development',
    state_explanation: 'No durable reference identity exists at extraction time.',
    blocking: false,
    allow_opt_out: false,
    field_path: 'single_reference',
  })

  return buildDomainEnvelopeMetadata({
    domain_pack_id: 'example.disease',
    domain_pack_version: '0.1.0',
    validation_attachments: [termLookup, subjectCheck, referenceFuture],
    validation_summary: { ...base.validation_summary, blocking: 1 },
    object_definitions: [
      {
        ...base.object_definitions[0],
        object_type: 'Annotation',
        display_name: 'Disease annotation',
        description: 'One evidence-backed disease assertion.',
        object_role: 'curatable_unit',
        fields: [
          { ...baseField, field_path: 'mention', display_name: 'Paper disease mention', required: true, source_of_truth: null, validation_attachments: [] },
          { ...baseField, field_path: 'term.name', display_name: 'Disease term name', required: true, source_of_truth: 'provider_a', validation_attachments: [termLookup] },
          { ...baseField, field_path: 'subject.identifier', display_name: 'Subject identifier', required: false, source_of_truth: 'provider_b', validation_attachments: [subjectCheck] },
          { ...baseField, field_path: 'single_reference', display_name: 'Source reference', required: false, validation_attachments: [referenceFuture] },
        ],
        field_groups: [
          { id: 'disease', label: 'Disease', field_paths: ['mention', 'term.name'] },
          { id: 'subject', label: 'Subject', field_paths: ['subject.identifier'] },
        ],
      },
      {
        ...base.object_definitions[0],
        object_type: 'Subject',
        display_name: 'Disease annotation subject',
        object_role: 'validated_reference',
        fields: [{ ...baseField, field_path: 'subject_label', display_name: 'Subject label', required: false, validation_attachments: [] }],
        field_groups: [],
      },
      {
        ...base.object_definitions[0],
        object_type: 'Term',
        display_name: 'Disease ontology term',
        object_role: 'validated_reference',
        fields: [{ ...baseField, field_path: 'curie', display_name: 'CURIE', required: true, validation_attachments: [] }],
        field_groups: [],
      },
    ],
  })
}

describe('EnvelopeTab', () => {
  it('selects the object that holds a focused field and marks its row', () => {
    render(<EnvelopeTab metadata={buildDiseaseLikeMetadata()} focus={{ objectType: 'Term', fieldPath: 'curie' }} />)

    const picker = screen.getByRole('group', { name: 'Envelope object' })
    expect(within(picker).getByRole('button', { name: 'Embedded references (2)' })).toHaveAttribute('aria-pressed', 'true')
    const row = screen.getByText('CURIE').closest('tr') as HTMLElement
    expect(row).toHaveAttribute('aria-current', 'true')
    expect(screen.getByText('Subject label').closest('tr')).not.toHaveAttribute('aria-current')
  })

  it('shows the produced object, a count line, a grouped table, validators, and closed provenance', () => {
    render(<EnvelopeTab metadata={buildDiseaseLikeMetadata()} />)

    expect(screen.getByText(/Produces/)).toHaveTextContent('Produces Disease annotation objects. One evidence-backed disease assertion.')
    const counts = screen.getByText(/validators active/).closest('p') as HTMLElement
    expect(counts).toHaveTextContent('2 validators active')
    expect(counts).toHaveTextContent('1 under development')
    expect(counts).toHaveTextContent('3 required fields')
    expect(counts).toHaveTextContent('1 blocking check')
    expect(counts).toHaveTextContent('Pack example.disease v0.1.0')

    const picker = screen.getByRole('group', { name: 'Envelope object' })
    const buttons = within(picker).getAllByRole('button')
    expect(buttons.map((button) => button.textContent)).toEqual(['Disease annotation', 'Embedded references (2)'])
    expect(buttons[0]).toHaveAttribute('aria-pressed', 'true')

    const table = screen.getByRole('table', { name: 'Disease annotation fields' })
    expect(within(table).getAllByRole('rowheader').map((header) => header.textContent)).toEqual(['Disease', 'Subject', 'Other fields'])
    expect(within(table).getByText('Term lookup')).toBeInTheDocument()
    expect(within(table).getByText('Blocking')).toBeInTheDocument()
    expect(within(table).getByText('Opt-out')).toBeInTheDocument()

    const validators = screen.getByRole('list', { name: 'Validators on this object' })
    const items = within(validators).getAllByRole('listitem')
    expect(items).toHaveLength(3)
    expect(items[2]).toHaveTextContent('No durable reference identity exists at extraction time.')

    expect(screen.getByRole('button', { name: 'Schema and provenance' })).toHaveAttribute('aria-expanded', 'false')

    // Removed elements from the old panel must not come back.
    expect(screen.queryByText(/semantic source of truth/)).not.toBeInTheDocument()
    expect(screen.queryByText(/source of truth is/)).not.toBeInTheDocument()
    expect(screen.queryByText('Validator capabilities')).not.toBeInTheDocument()
    expect(screen.queryByText('Schema references')).not.toBeInTheDocument()
  })

  it('switches to the embedded references entry and groups rows by object', () => {
    render(<EnvelopeTab metadata={buildDiseaseLikeMetadata()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Embedded references (2)' }))

    const table = screen.getByRole('table', { name: 'Embedded references (2) fields' })
    expect(within(table).getAllByRole('rowheader').map((header) => header.textContent)).toEqual([
      'Disease annotation subject',
      'Disease ontology term',
    ])
    expect(screen.getByText('No automatic checks run on this object.')).toBeInTheDocument()
  })

  it('renders a flat table when the object declares no field groups', () => {
    render(<EnvelopeTab metadata={buildDomainEnvelopeMetadata({
      object_definitions: [{ ...buildDomainEnvelopeMetadata().object_definitions[0], field_groups: [] }],
    })} />)

    const table = screen.getByRole('table', { name: 'Gene mention evidence fields' })
    expect(within(table).queryAllByRole('rowheader')).toHaveLength(0)
    expect(within(table).getByText('Gene symbol')).toBeInTheDocument()
  })

  it('hides the type column at narrow width', () => {
    render(<EnvelopeTab metadata={buildDomainEnvelopeMetadata()} narrow />)
    expect(screen.queryByRole('columnheader', { name: 'Type · source' })).not.toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Automatic check' })).toBeInTheDocument()
  })
})
