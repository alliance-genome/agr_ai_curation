import { useState } from 'react'
import { fireEvent, render, screen, within, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { GenericProfileContract } from '@/services/genericProfileService'
import OutputStructureEditor, { type OutputStructureEditorProps } from './OutputStructureEditor'

const initial: GenericProfileContract = {
  name: 'Example structure', semantic_class: 'record', fields: [
    { key: 'title', display_name: 'Title', description: 'Keep the wording from the paper.', source_labels: ['Paper heading'], value_schema: { kind: 'string' } },
    { key: 'sources', display_name: 'Sources', nullable: true, value_schema: {
      kind: 'object', fields: [{ key: 'name', display_name: 'Source name', value_schema: { kind: 'string' } }],
    } },
  ],
}
function Harness(props: Partial<OutputStructureEditorProps>) {
  const [value, setValue] = useState(props.value ?? structuredClone(initial))
  return <><OutputStructureEditor {...props} value={value} onChange={(next) => { setValue(next); props.onChange?.(next) }} issues={props.issues ?? []} onValidate={props.onValidate ?? (() => undefined)} />
    <output aria-label="Current draft">{JSON.stringify(value)}</output></>
}
const draft = (): GenericProfileContract => JSON.parse(screen.getByLabelText('Current draft').textContent!)
function edit(name = 'Title') { fireEvent.click(screen.getByRole('button', { name: `Edit ${name}` })) }

describe('Curator collection overview', () => {
  it('starts a custom item type without reagent-specific fields or a technical class question', () => {
    render(<Harness value={{ name: '', semantic_class: '', fields: [] }} />)
    fireEvent.change(screen.getByLabelText('Type of item'), { target: { value: 'Antibodies' } })
    fireEvent.click(screen.getByRole('button', { name: 'Choose details to collect' }))
    expect(draft()).toMatchObject({ name: 'Antibodies', semantic_class: 'antibodies', fields: [] })
    expect(screen.getByText('What do you want to know about each item?')).toBeVisible()
    expect(screen.queryByLabelText('Record class')).not.toBeInTheDocument()
  })
  it('preserves existing internal class when naming a draft', () => {
    render(<Harness value={{ name: '', semantic_class: 'saved_class', fields: initial.fields }} />)
    fireEvent.change(screen.getByLabelText('Type of item'), { target: { value: 'Reagents' } })
    fireEvent.click(screen.getByRole('button', { name: 'Choose details to collect' }))
    expect(draft()).toMatchObject({ name: 'Reagents', semantic_class: 'saved_class', fields: initial.fields })
  })
  it('starts read-only with descriptions available through accessible help', () => {
    render(<Harness />)
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'About Title' }))
    expect(screen.getByText('Keep the wording from the paper.')).toBeVisible()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(draft()).toEqual(initial)
  })
  it('renames a display field without changing its key or source aliases', () => {
    const validate = vi.fn(); render(<Harness onValidate={validate} />); edit()
    fireEvent.change(screen.getByLabelText('Detail name'), { target: { value: 'Paper title' } })
    expect(validate).not.toHaveBeenCalled()
    fireEvent.blur(screen.getByLabelText('Detail name'))
    expect(validate).toHaveBeenCalledOnce()
    expect(draft().fields[0]).toMatchObject({ key: 'title', display_name: 'Paper title', source_labels: ['Paper heading'] })
    expect(screen.queryByLabelText('Output key')).not.toBeInTheDocument()
  })
  it('requires an explicit action to edit extraction instructions', () => {
    render(<Harness />); edit()
    expect(screen.queryByLabelText('Instructions for the agent')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Edit instructions' }))
    fireEvent.change(screen.getByLabelText('Instructions for the agent'), { target: { value: 'Preserve punctuation.' } })
    expect(draft().fields[0].description).toBe('Preserve punctuation.')
  })
  it('generates unique keys from new names, including collisions with source aliases', () => {
    render(<Harness value={{ ...initial, fields: [{ ...initial.fields[0], source_labels: ['Detail paper heading'] }] }} />)
    fireEvent.click(screen.getByRole('button', { name: 'Add a detail' }))
    fireEvent.change(screen.getByLabelText('New detail name'), { target: { value: 'Paper heading' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add detail' }))
    expect(draft().fields[1]).toMatchObject({ key: 'detail_paper_heading_2', display_name: 'Paper heading', source_labels: [], required: false, nullable: false })
  })
  it('keeps required and unknown-answer settings independent', () => {
    render(<Harness />); edit('Sources')
    fireEvent.click(screen.getByRole('checkbox', { name: 'Ask for this in every record' }))
    expect(draft().fields[1]).toMatchObject({ required: true, nullable: true })
    fireEvent.click(screen.getByRole('button', { name: 'More field options' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Allow an empty answer if the paper doesn’t say' }))
    expect(draft().fields[1]).toMatchObject({ required: true, nullable: false })
  })
  it('shows concrete formats and preserves nested data when replacement is canceled', () => {
    render(<Harness />); edit('Sources')
    expect(screen.getByRole('radio', { name: /An answer with several parts/ })).toBeChecked()
    expect(screen.queryByRole('checkbox', { name: 'Collect more than one answer for this detail' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('radio', { name: /^Text/ }))
    expect(screen.getByRole('dialog', { name: 'Replace this answer format?' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(draft()).toEqual(initial)
  })
  it('converts an existing list only after confirmation and preserves its choices', async () => {
    const value: GenericProfileContract = { ...initial, fields: [{ key: 'status', display_name: 'Status', value_schema: { kind: 'array', items: { kind: 'enum', values: ['new', 'existing'] } } }] }
    render(<Harness value={value} />); edit('Status')
    expect(draft()).toEqual(value)
    fireEvent.click(screen.getByRole('button', { name: 'Change to one answer' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(draft()).toEqual(value)
    fireEvent.click(await screen.findByRole('button', { name: 'Change to one answer' }))
    fireEvent.click(screen.getByRole('button', { name: 'Change format' }))
    expect(draft().fields[0].value_schema).toEqual({ kind: 'enum', values: ['new', 'existing'] })
    expect(screen.queryByRole('checkbox', { name: 'Collect more than one answer for this detail' })).not.toBeInTheDocument()
  })
  it('explains that item guidance supplements the existing agent prompt', () => {
    render(<Harness />)
    expect(screen.getByText(/in addition to your agent prompt and detail instructions/)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Edit item description' }))
    fireEvent.change(screen.getByLabelText('Additional guidance for this item type'), { target: { value: 'Include living stocks.' } })
    expect(draft().description).toBe('Include living stocks.')
    expect(screen.getByText(/Add a brief description to supplement your existing agent prompt/)).toBeVisible()
  })
  it('renders a recognizable empty table with headers and an empty row', () => {
    render(<Harness value={{ ...initial, fields: [] }} />)
    const table = screen.getByRole('table', { name: 'Details to collect' })
    expect(within(table).getByRole('columnheader', { name: 'Detail' })).toBeVisible()
    expect(within(table).getByRole('columnheader', { name: 'What to collect' })).toBeVisible()
    expect(within(table).getByRole('columnheader', { name: 'Include' })).toBeVisible()
    expect(within(table).getByText('No details yet')).toBeVisible()
  })
  it('edits nested values and updates the example without a separate review copy', async () => {
    render(<Harness />); edit('Source name')
    fireEvent.change(screen.getByLabelText('Detail name'), { target: { value: 'Supplier' } })
    fireEvent.click(screen.getByRole('button', { name: 'Back to all details' }))
    fireEvent.click(await screen.findByRole('tab', { name: 'Example record' }))
    expect(within(screen.getByRole('tabpanel')).getByText('Supplier')).toBeVisible()
    expect(draft().fields[1].value_schema).toMatchObject({ fields: [{ key: 'name', display_name: 'Supplier' }] })
  })
  it('confirms removal and preserves the rest of the collection', () => {
    render(<Harness />); edit(); fireEvent.click(screen.getByRole('button', { name: 'More field options' }))
    fireEvent.click(screen.getByRole('button', { name: 'Remove field' }))
    const dialog = screen.getByRole('dialog', { name: 'Remove this field from the draft?' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove field' }))
    expect(draft().fields).toEqual([initial.fields[1]])
  })
  it('routes a nested error to its editor without losing the draft', async () => {
    render(<Harness issues={[{ path: 'fields[1].value_schema.fields[0].description', code: 'invalid', message: 'Clarify this instruction' }]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Clarify this instruction' }))
    expect(screen.getByRole('region', { name: 'Edit Source name' })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByLabelText('Instructions for the agent')).toHaveFocus())
    expect(draft()).toEqual(initial)
  })
  it('keeps a new part in its parent and opens its editor only on request', () => {
    render(<Harness />); edit('Sources')
    fireEvent.click(screen.getByRole('button', { name: 'Add another part' }))
    fireEvent.change(screen.getByLabelText('New detail name'), { target: { value: 'Identifier' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add detail' }))
    expect(screen.getByRole('region', { name: 'Edit Sources' })).toBeVisible()
    expect(screen.queryByRole('region', { name: 'Edit Identifier' })).not.toBeInTheDocument()
    expect(within(screen.getByRole('table', { name: 'Parts of Sources' })).getByRole('rowheader', { name: 'Identifier' })).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Edit part Identifier' }))
    expect(screen.getByRole('region', { name: 'Edit Identifier' })).toBeVisible()
    expect(screen.queryByRole('radio', { name: /An answer with several parts/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Add another part' })).not.toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /^Text/ })).toBeChecked()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Detail location' })).toHaveTextContent('Sources')
    fireEvent.click(screen.getByRole('checkbox', { name: 'Include this part whenever the answer is included' }))
    fireEvent.click(screen.getAllByRole('button', { name: 'Done — back to Sources' })[0])
    expect(screen.getByRole('region', { name: 'Edit Sources' })).toBeVisible()
    expect(screen.getByRole('radio', { name: /An answer with several parts/ })).toBeChecked()
    expect(draft().fields[1].value_schema).toMatchObject({ kind: 'object', fields: [{ key: 'name' }, { key: 'detail_identifier', required: true, value_schema: { kind: 'string' } }] })
  })
  it('shows an empty parts table and collects two required parts in the same answer', () => {
    render(<Harness value={{ ...initial, fields: [{ key: 'stock', display_name: 'Stock', value_schema: { kind: 'object', fields: [] } }] }} />)
    edit('Stock')
    const table = screen.getByRole('table', { name: 'Parts of Stock' })
    expect(within(table).getByRole('columnheader', { name: 'Part' })).toBeVisible()
    expect(within(table).getByRole('columnheader', { name: /Always include/ })).toBeVisible()
    expect(within(table).getByText('No parts yet')).toBeVisible()
    for (const [index, name] of ['Supplier name', 'Catalog number'].entries()) {
      fireEvent.click(screen.getByRole('button', { name: index ? 'Add another part' : 'Add the first part' }))
      fireEvent.change(screen.getByLabelText('New detail name'), { target: { value: name } })
      fireEvent.click(screen.getByRole('button', { name: 'Add detail' }))
      expect(screen.getByRole('region', { name: 'Edit Stock' })).toBeVisible()
      expect(screen.queryByRole('region', { name: `Edit ${name}` })).not.toBeInTheDocument()
      fireEvent.click(screen.getByRole('checkbox', { name: `Always include ${name} with this answer` }))
    }
    expect(draft().fields).toHaveLength(1)
    expect(draft().fields[0].value_schema).toMatchObject({ kind: 'object', fields: [
      { display_name: 'Supplier name', required: true },
      { display_name: 'Catalog number', required: true },
    ] })
  })
  it('explains always-include and missing answers without editing the draft', async () => {
    render(<Harness />); edit('Sources')
    fireEvent.click(screen.getByRole('button', { name: 'Help with Always include' }))
    expect(screen.getByText('What does “Always include” mean?')).toBeVisible()
    expect(screen.getByText(/The AI must not invent missing information/)).toBeVisible()
    expect(draft()).toEqual(initial)
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(screen.queryByText('What does “Always include” mean?')).not.toBeInTheDocument())
    expect(screen.getByRole('table', { name: 'Parts of Sources' })).toBeVisible()
  })
  it('disables mutation entry points while the parent owns the draft', () => {
    const changed = vi.fn(); render(<Harness disabled onChange={changed} />)
    for (const name of ['Edit Title', 'Edit Sources', 'Add a detail', 'Edit item description']) expect(screen.getByRole('button', { name })).toBeDisabled()
    expect(changed).not.toHaveBeenCalled()
  })
})
