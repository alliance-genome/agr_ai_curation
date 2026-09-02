import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import EnvelopeFieldTable from './EnvelopeFieldTable'
import { groupObjectFields } from './envelopePresentation'
import {
  buildDomainEnvelopeMetadata,
  buildValidationAttachmentOption,
} from '@/test/fixtures/agentStudioDomainEnvelope'
import type { DomainEnvelopeFieldMetadata, DomainEnvelopeObjectMetadata } from '@/services/agentStudioService'

const baseObject = buildDomainEnvelopeMetadata().object_definitions[0]
const baseField = baseObject.fields[0]

function field(overrides: Partial<DomainEnvelopeFieldMetadata>): DomainEnvelopeFieldMetadata {
  return { ...baseField, validation_attachments: [], ...overrides }
}

function object(overrides: Partial<DomainEnvelopeObjectMetadata>): DomainEnvelopeObjectMetadata {
  return { ...baseObject, ...overrides }
}

describe('EnvelopeFieldTable', () => {
  it('renders a real table with required dots, type and source words, and automatic checks', () => {
    const blocking = buildValidationAttachmentOption({ label: 'Symbol lookup', blocking: true, allow_opt_out: false })
    const optOut = buildValidationAttachmentOption({ attachment_id: 'x:opt', validator_id: 'x', label: 'Relation lookup', blocking: false, allow_opt_out: true })
    const future = buildValidationAttachmentOption({ attachment_id: 'x:dev', validator_id: 'dev', label: 'Reference materialization', state: 'under_development', blocking: false, allow_opt_out: false })
    const groups = groupObjectFields(object({
      fields: [
        field({ field_path: 'symbol', display_name: 'Gene symbol', required: true, source_of_truth: 'alliance_linkml', validation_attachments: [blocking] }),
        field({ field_path: 'relation', display_name: 'Relation', required: false, field_type: 'enum', enum_ref: 'RelationName', source_of_truth: 'curation_db', validation_attachments: [optOut] }),
        field({ field_path: 'reference', display_name: 'Reference', required: false, source_of_truth: null, validation_attachments: [future] }),
        field({ field_path: 'mention', display_name: 'Mention', required: true, source_of_truth: undefined }),
      ],
      field_groups: [],
    }))

    render(<EnvelopeFieldTable groups={groups} ariaLabel="Gene fields" />)

    const table = screen.getByRole('table', { name: 'Gene fields' })
    expect(within(table).getByRole('columnheader', { name: /Req/ })).toBeInTheDocument()
    expect(within(table).getByRole('columnheader', { name: 'Type · source' })).toBeInTheDocument()
    expect(within(table).getByRole('columnheader', { name: 'Automatic check' })).toBeInTheDocument()
    expect(within(table).getAllByRole('img', { name: 'Required' })).toHaveLength(2)

    expect(within(table).getAllByText('string')).toHaveLength(3)
    expect(within(table).getByText('· LinkML')).toBeInTheDocument()
    expect(within(table).getByText('choice: RelationName')).toBeInTheDocument()
    expect(within(table).getByText('· Curation DB')).toBeInTheDocument()
    expect(within(table).getAllByText('· Extractor')).toHaveLength(2)

    expect(within(table).getByText('Symbol lookup')).toBeInTheDocument()
    expect(within(table).getByText('Blocking')).toBeInTheDocument()
    expect(within(table).getByText('Opt-out')).toBeInTheDocument()
    expect(within(table).getByRole('img', { name: 'Under development' })).toBeInTheDocument()
    expect(within(table).getByText('Not checked')).toBeInTheDocument()
    expect(within(table).queryByRole('rowgroup', { name: /Other fields/ })).not.toBeInTheDocument()
  })

  it('groups rows under declared field-group labels', () => {
    const groups = groupObjectFields(object({
      fields: [
        field({ field_path: 'a', display_name: 'A' }),
        field({ field_path: 'b', display_name: 'B' }),
        field({ field_path: 'c', display_name: 'C' }),
      ],
      field_groups: [
        { id: 'identity', label: 'Identity', field_paths: ['a', 'b'] },
      ],
    }))

    render(<EnvelopeFieldTable groups={groups} ariaLabel="Grouped fields" />)

    const table = screen.getByRole('table', { name: 'Grouped fields' })
    const groupHeaders = within(table).getAllByRole('rowheader')
    expect(groupHeaders.map((header) => header.textContent)).toEqual(['Identity', 'Other fields'])
  })

  it('hides the type column and keeps validation at narrow width', () => {
    const groups = groupObjectFields(object({ field_groups: [] }))
    render(<EnvelopeFieldTable groups={groups} ariaLabel="Narrow fields" narrow />)

    const table = screen.getByRole('table', { name: 'Narrow fields' })
    expect(within(table).queryByRole('columnheader', { name: 'Type · source' })).not.toBeInTheDocument()
    expect(within(table).getByRole('columnheader', { name: 'Automatic check' })).toBeInTheDocument()
    expect(within(table).getByText('Gene lookup')).toBeInTheDocument()
  })

  it('exposes long field paths through a title attribute', () => {
    const longPath = 'disease_annotation_subject.subject_identifier.very.long.nested.path.that.overflows'
    const groups = groupObjectFields(object({ fields: [field({ field_path: longPath, display_name: 'Subject identifier' })], field_groups: [] }))
    render(<EnvelopeFieldTable groups={groups} ariaLabel="Long fields" />)
    expect(screen.getByTitle(longPath)).toBeInTheDocument()
  })

  it('states when the object declares no fields', () => {
    render(<EnvelopeFieldTable groups={[{ id: 'x', label: null, fields: [] }]} ariaLabel="Empty" />)
    expect(screen.getByText('This object declares no fields.')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
})
