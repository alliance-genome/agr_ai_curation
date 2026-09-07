import { useState } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import WorkshopOutputSetup from './WorkshopOutputSetup'
import { emptyOutputDraft, type WorkshopOutputDraft } from './workshopOutputDraft'
import { buildDomainEnvelopeMetadata } from '@/test/fixtures/agentStudioDomainEnvelope'

function Harness({ initial = emptyOutputDraft() }: { initial?: WorkshopOutputDraft }) {
  const [value, onChange] = useState(initial)
  return <>
    <WorkshopOutputSetup value={value} onChange={onChange} onEditStructure={vi.fn()} agents={{
      facts: { name: 'Facts', icon: '', category: 'Extraction', output_schema_key: 'facts',
        domain_envelope: { ...buildDomainEnvelopeMetadata(), display_name: 'Facts', status: 'under_development' } },
      builder: { name: 'Builder', icon: '', category: 'Extraction', output_schema_key: null,
        domain_extraction_ref: { package_id: 'fixture.package', agent_id: 'builder', domain_pack_id: 'fixture.domain' } },
    }} />
    <output aria-label="Selected output mode">{value.mode}</output>
    <output aria-label="Selected builder">{value.domainExtractionRef?.agent_id ?? 'none'}</output>
    <output aria-label="Selected schema">{value.schemaKey || 'none'}</output>
  </>
}

describe('Workshop output choices', () => {
  it('selects a schema-null packaged builder and explicitly clears it when changing format', async () => {
    render(<Harness initial={emptyOutputDraft('domain')} />)
    fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Domain format' }))
    fireEvent.click(await screen.findByRole('option', { name: 'Builder — support details unavailable' }))
    expect(screen.getByLabelText('Selected builder')).toHaveTextContent('builder')
    expect(screen.getByLabelText('Selected schema')).toHaveTextContent('none')
    expect(screen.getByText(/not a model-response schema/)).toBeInTheDocument()
    fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Domain format' }))
    fireEvent.click(await screen.findByRole('option', { name: 'Facts — under_development' }))
    expect(screen.getByLabelText('Selected builder')).toHaveTextContent('none')
    expect(screen.getByLabelText('Selected schema')).toHaveTextContent('facts')
  })

  it('starts with explicit no-output and offers a new custom structure for extraction', () => {
    render(<Harness />)
    expect(screen.getByRole('radio', { name: 'No structured output' })).toBeChecked()
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('radio', { name: 'Structured extraction' }))
    expect(screen.getByLabelText('Selected output mode')).toHaveTextContent('profile_bound_generic')
    expect(screen.getByRole('button', { name: 'Edit Output Structure' })).toBeInTheDocument()
    expect(screen.getByText(/Not saved yet/)).toBeInTheDocument()
  })

  it('does not infer flexible extraction from an empty domain selection', async () => {
    render(<Harness initial={emptyOutputDraft('domain')} />)
    expect(screen.getByLabelText('Selected output mode')).toHaveTextContent('domain')
    fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Domain format' }))
    const format = await screen.findByRole('option', { name: 'Facts — under_development' })
    expect(format).not.toHaveAttribute('aria-disabled', 'true')
    fireEvent.click(format)
    expect(screen.getByRole('combobox', { name: 'Domain format' })).toHaveTextContent('Facts')
  })

  it('distinguishes flexible generic extraction from no output', () => {
    render(<Harness initial={emptyOutputDraft('unprofiled_generic')} />)
    expect(screen.getByRole('radio', { name: 'Structured extraction' })).toBeChecked()
    expect(screen.getByRole('alert')).toHaveTextContent('fields it considers useful')
    fireEvent.click(screen.getByRole('radio', { name: 'No structured output' }))
    expect(screen.getByLabelText('Selected output mode')).toHaveTextContent('none')
  })

  it('preserves the current structure when a format change is canceled', () => {
    const initial = emptyOutputDraft('profile_bound_generic')
    initial.profileContract!.name = 'Curator details'
    render(<Harness initial={initial} />)
    fireEvent.click(screen.getByRole('radio', { name: 'No structured output' }))
    expect(screen.getByRole('dialog', { name: 'Change output format?' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }))
    expect(screen.getByLabelText('Selected output mode')).toHaveTextContent('profile_bound_generic')
    expect(screen.getByText(/Curator details/)).toBeInTheDocument()
  })
})
