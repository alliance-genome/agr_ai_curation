import { useState } from 'react'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { GenericProfileContract } from '@/services/genericProfileService'
import OutputStructureEditor, { type OutputStructureEditorProps } from './OutputStructureEditor'

const initial: GenericProfileContract = {
  name: 'Example structure', semantic_class: 'record', fields: [
    { key: 'title', display_name: 'Title', value_schema: { kind: 'string' } },
    { key: 'sources', display_name: 'Sources', value_schema: { kind: 'array', items: {
      kind: 'object', fields: [{ key: 'name', display_name: 'Source name', value_schema: { kind: 'string' } }],
    } } },
  ],
}

function Harness(props: Partial<OutputStructureEditorProps>) {
  const [value, setValue] = useState(props.value ?? structuredClone(initial))
  return <><OutputStructureEditor {...props} value={value} onChange={(next) => { setValue(next); props.onChange?.(next) }}
    issues={props.issues ?? []} onValidate={props.onValidate ?? (() => undefined)} />
    <output aria-label="Current test draft">{JSON.stringify(value)}</output></>
}

function draft(): GenericProfileContract {
  return JSON.parse(screen.getByLabelText('Current test draft').textContent!)
}

function choose(label: string, option: string) {
  fireEvent.mouseDown(screen.getByRole('combobox', { name: label }))
  fireEvent.click(screen.getByRole('option', { name: option }))
}

describe('Output Structure editor', () => {
  it('reviews the canonical draft and returns to a nested field without JSON or lost edits', () => {
    render(<Harness />)
    const review = screen.getByRole('region', { name: 'Review before saving' })
    expect(within(review).getAllByText(/May be absent.*Explicit unknown \(null\) not allowed/)).toHaveLength(3)
    fireEvent.click(within(review).getByRole('button', { name: 'Change Source name' }))
    expect(screen.getByRole('textbox', { name: 'Field name' })).toHaveFocus()
    fireEvent.change(screen.getByRole('textbox', { name: 'Field name' }), { target: { value: 'Source detail' } })
    expect(within(review).getByRole('button', { name: 'Change Source detail' })).toBeInTheDocument()
    fireEvent.click(within(review).getByRole('button', { name: 'Change structure basics' }))
    expect(screen.getByRole('textbox', { name: /Structure name/ })).toHaveFocus()
    expect(draft().fields[1].value_schema).toMatchObject({ items: { fields: [{ display_name: 'Source detail' }] } })
    expect(screen.getByRole('button', { name: 'Show technical JSON preview' })).toBeInTheDocument()
  })

  it('explains closed attributes and locked fields without requiring a JSON editor', () => {
    render(<Harness />)
    expect(screen.getByText(/Only defined fields are accepted/)).toBeInTheDocument()
    expect(screen.getByText('Locked platform fields')).toBeInTheDocument()
    expect(screen.getByText('Placeholder data, not paper evidence')).toBeInTheDocument()
    expect(screen.getByLabelText('Field name')).toHaveValue('Title')
    expect(screen.queryByRole('textbox', { name: /JSON/ })).not.toBeInTheDocument()
    expect(screen.queryByText(/allow extra fields/i)).not.toBeInTheDocument()
  })

  it('keeps typed input and validates on blur, not on each keystroke', () => {
    const validate = vi.fn()
    render(<Harness onValidate={validate} />)
    fireEvent.change(screen.getByLabelText('Field name'), { target: { value: 'Entered name' } })
    expect(validate).not.toHaveBeenCalled()
    fireEvent.blur(screen.getByLabelText('Field name'))
    expect(validate).toHaveBeenCalledOnce()
    expect(draft().fields[0].display_name).toBe('Entered name')
  })

  it('keeps required and nullable separate', () => {
    render(<Harness />)
    fireEvent.click(screen.getByRole('checkbox', { name: /Required/ }))
    expect(draft().fields[0]).toMatchObject({ required: true })
    expect(draft().fields[0].nullable).toBeUndefined()
    fireEvent.click(screen.getByRole('checkbox', { name: /explicit unknown/ }))
    expect(draft().fields[0]).toMatchObject({ required: true, nullable: true })
  })

  it('adds, duplicates and reorders using visible keyboard-operable buttons', () => {
    render(<Harness value={{ ...initial, fields: [] }} />)
    fireEvent.click(screen.getByRole('button', { name: 'Add field' }))
    fireEvent.change(screen.getByLabelText('Field name'), { target: { value: 'A detail' } })
    fireEvent.click(screen.getByRole('button', { name: 'Duplicate' }))
    expect(draft().fields.map((field) => field.key)).toEqual(['new_field', 'new_field_copy'])
    fireEvent.click(screen.getByRole('button', { name: 'Move up' }))
    expect(draft().fields.map((field) => field.key)).toEqual(['new_field_copy', 'new_field'])
    expect(screen.getByRole('button', { name: 'Move up' })).toBeDisabled()
  })

  it('edits child fields and preserves the surrounding repeating group', () => {
    render(<Harness />)
    fireEvent.click(within(screen.getByRole('list', { name: 'Custom fields' })).getByRole('button', { name: /Sources/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Add child field' }))
    fireEvent.change(screen.getByLabelText('Field name'), { target: { value: 'Supplier' } })
    const schema = draft().fields[1].value_schema
    expect(schema).toMatchObject({ kind: 'array', items: { kind: 'object', fields: [
      { key: 'name' }, { key: 'new_field', display_name: 'Supplier' },
    ] } })
  })

  it('offers editable enum choices inside a list without JSON', () => {
    render(<Harness />)
    choose('Value kind', 'List of values')
    choose('List item value kind', 'Choose from a list')
    fireEvent.change(screen.getByLabelText('Allowed choices — one per line'), { target: { value: 'reported\nunknown' } })
    expect(draft().fields[0].value_schema).toEqual({ kind: 'array', items: { kind: 'enum', values: ['reported', 'unknown'] } })
  })

  it('requires confirmation before replacing nested list fields and preserves them on cancel', () => {
    render(<Harness />)
    fireEvent.click(within(screen.getByRole('list', { name: 'Custom fields' })).getByRole('button', { name: /Sources/ }))
    choose('List item value kind', 'Text')
    expect(screen.getByRole('dialog', { name: 'Replace this value structure?' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(draft().fields[1]).toEqual(initial.fields[1])
  })

  it('confirms subtree removal and retains saved-version copy', () => {
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: 'Remove field' }))
    const dialog = screen.getByRole('dialog', { name: 'Remove this field from the draft?' })
    expect(within(dialog).getByText(/Saved revisions remain unchanged/)).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove field' }))
    expect(draft().fields.map((field) => field.key)).toEqual(['sources'])
  })

  it('shows source labels as aliases while the preview keeps one canonical key', () => {
    render(<Harness />)
    fireEvent.click(screen.getByText('Technical key and source labels'))
    fireEvent.change(screen.getByLabelText('Synonyms / source labels (not output fields)'), { target: { value: 'Paper heading' } })
    fireEvent.click(screen.getByRole('button', { name: 'Show technical JSON preview' }))
    expect(document.querySelector('pre')!.textContent).toContain('"title"')
    expect(document.querySelector('pre')!.textContent).not.toContain('Paper heading')
    expect(draft().fields[0].source_labels).toEqual(['Paper heading'])
  })

  it('announces linked errors without discarding the controlled draft', () => {
    render(<Harness issues={[{ path: 'fields[1].value_schema.items.fields[0].key', code: 'key', message: 'Use a canonical key' }]} />)
    fireEvent.click(screen.getByRole('button', { name: /Use a canonical key/ }))
    expect(screen.getByLabelText('Field name')).toHaveValue('Source name')
    expect(screen.getByLabelText('Output key')).toHaveFocus()
    expect(screen.getByLabelText('Output key')).toHaveAccessibleDescription('Use a canonical key')
    expect(draft()).toEqual(initial)
  })

  it('associates basic field errors with their inputs for assistive technology', () => {
    render(<Harness issues={[
      { path: 'name', code: 'required', message: 'Enter a structure name' },
      { path: 'semantic_class', code: 'required', message: 'Enter a record class' },
      { path: 'fields[0].display_name', code: 'invalid', message: 'Check the field name' },
    ]} />)
    expect(screen.getByRole('textbox', { name: /Structure name/ })).toBeInvalid()
    expect(screen.getByRole('textbox', { name: /Structure name/ })).toHaveAccessibleDescription('Enter a structure name')
    expect(screen.getByRole('textbox', { name: /Record class/ })).toHaveAccessibleDescription('Enter a record class')
    expect(screen.getByRole('textbox', { name: 'Field name' })).toHaveAccessibleDescription('Check the field name')
  })

  it('disables value-kind selects recursively while saves own the draft', () => {
    const changed = vi.fn()
    render(<Harness disabled onChange={changed} value={{ ...initial, fields: [initial.fields[1]] }} />)
    for (const select of screen.getAllByRole('combobox')) {
      expect(select).toHaveAttribute('aria-disabled', 'true')
      fireEvent.mouseDown(select)
    }
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(changed).not.toHaveBeenCalled()
  })
})
