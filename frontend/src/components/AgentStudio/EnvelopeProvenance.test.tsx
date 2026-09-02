import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import EnvelopeProvenance from './EnvelopeProvenance'
import { buildDomainEnvelopeMetadata } from '@/test/fixtures/agentStudioDomainEnvelope'

const COMMIT = '1b11d0888f19eba4ca72022200bb7d96b30d4a52'

function buildMetadata() {
  const base = buildDomainEnvelopeMetadata()
  return buildDomainEnvelopeMetadata({
    domain_pack_id: 'example.pack',
    domain_pack_version: '0.1.0',
    status: 'active',
    schema_refs: [
      {
        schema_id: 'example.linkml',
        provider: 'alliance_linkml',
        name: 'Example LinkML schema',
        version: COMMIT,
        uri: `https://example.test/schema/tree/${COMMIT}`,
      },
    ],
    object_definitions: [
      {
        ...base.object_definitions[0],
        object_type: 'Annotation',
        display_name: 'Annotation',
        schema_ref: null,
        definition_notes: ['Abstract parent; concrete subtypes are the write targets.'],
        provider_refs: {
          alliance_linkml: {
            schema_ref: 'example.linkml',
            commit: COMMIT,
            source_file: 'model/schema/annotation.yaml',
            class: 'Annotation',
          },
          some_db: {
            inspected_tables: ['public.annotation', 'public.subject'],
            nested: { ignored: true },
          },
        },
      },
    ],
  })
}

describe('EnvelopeProvenance', () => {
  it('is closed by default and opens into a definition list with links at the pinned commit', () => {
    const metadata = buildMetadata()
    render(<EnvelopeProvenance metadata={metadata} objects={metadata.object_definitions} />)

    const toggle = screen.getByRole('button', { name: 'Schema and provenance' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('Domain pack')).not.toBeInTheDocument()

    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')

    expect(screen.getByText('Domain pack')).toBeInTheDocument()
    expect(screen.getByText('example.pack')).toBeInTheDocument()
    expect(screen.getByText(/v0\.1\.0, active/)).toBeInTheDocument()

    expect(screen.getByText('LinkML schema')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Example LinkML schema' })).toHaveAttribute('href', `https://example.test/schema/tree/${COMMIT}`)

    expect(screen.getByText('LinkML class')).toBeInTheDocument()
    expect(screen.getByText('Annotation', { selector: 'dd' })).toBeInTheDocument()
    expect(screen.getByText('LinkML source file')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /model\/schema\/annotation\.yaml/ })).toHaveAttribute(
      'href',
      `https://example.test/schema/tree/${COMMIT}/model/schema/annotation.yaml`
    )
    expect(screen.getByText('LinkML commit')).toBeInTheDocument()
    expect(screen.getAllByText('1b11d088').length).toBeGreaterThan(0)

    expect(screen.getByText('some_db inspected tables')).toBeInTheDocument()
    expect(screen.getByText('public.annotation, public.subject')).toBeInTheDocument()
    expect(screen.queryByText(/ignored/)).not.toBeInTheDocument()

    expect(screen.getByText('Notes')).toBeInTheDocument()
    expect(screen.getByText('Abstract parent; concrete subtypes are the write targets.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /alliance_linkml/ })).not.toBeInTheDocument()
  })

  it('names each object when several are shown together', () => {
    const metadata = buildMetadata()
    const second = { ...metadata.object_definitions[0], object_type: 'Subject', display_name: 'Subject' }
    render(<EnvelopeProvenance metadata={metadata} objects={[metadata.object_definitions[0], second]} />)

    fireEvent.click(screen.getByRole('button', { name: 'Schema and provenance' }))
    expect(screen.getByRole('heading', { level: 4, name: 'Annotation' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 4, name: 'Subject' })).toBeInTheDocument()
  })
})
