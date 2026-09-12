import { useState } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import ProfileConstantInput from './ProfileConstantInput'
import { canonicalAuthoringJson } from '../authoringContext'
import type { GenericProfileValueSchema } from '@/services/genericProfileService'

function Harness({ schema, changed, nullable = false }: { schema: GenericProfileValueSchema; changed: (value: unknown) => void; nullable?: boolean }) {
  const [value, setValue] = useState<unknown>(undefined)
  return <ProfileConstantInput label="Fixed context" schema={schema} nullable={nullable} value={value} onBlur={vi.fn()}
    onChange={(next) => { canonicalAuthoringJson({ constant: next }); changed(next); setValue(next) }} />
}

describe('typed mapping constants', () => {
  it('edits repeating objects and optional values without raw JSON or undefined array entries', () => {
    const changed = vi.fn()
    render(<Harness changed={changed} schema={{ kind: 'array', items: { kind: 'object', fields: [
      { key: 'name', required: true, value_schema: { kind: 'string' } },
      { key: 'count', value_schema: { kind: 'integer' } },
    ] } }} />)
    fireEvent.click(screen.getByRole('button', { name: 'Add item to Fixed context' }))
    expect(changed).toHaveBeenLastCalledWith([null])
    fireEvent.change(screen.getByRole('textbox', { name: 'Fixed context · item 1 · name' }), { target: { value: 'Explicit name' } })
    fireEvent.click(screen.getByRole('checkbox', { name: 'Include count' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Fixed context · item 1 · count' }), { target: { value: '4' } })
    expect(changed).toHaveBeenLastCalledWith([{ name: 'Explicit name', count: 4 }])
    fireEvent.click(screen.getByRole('button', { name: 'Remove item 1' }))
    expect(changed).toHaveBeenLastCalledWith([])
  })

  it('keeps a cleared numeric input invalid instead of changing it to zero', () => {
    const changed = vi.fn()
    render(<Harness changed={changed} schema={{ kind: 'number' }} />)
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '1.5' } })
    expect(changed).toHaveBeenLastCalledWith(1.5)
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '' } })
    expect(changed).toHaveBeenLastCalledWith('')
  })

  it('distinguishes explicit null from an unselected boolean', () => {
    const changed = vi.fn()
    render(<Harness changed={changed} nullable schema={{ kind: 'boolean' }} />)
    expect(changed).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Fixed context: explicit unknown (null)' }))
    expect(changed).toHaveBeenLastCalledWith(null)
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Fixed context: explicit unknown (null)' }))
    fireEvent.mouseDown(screen.getByRole('combobox'))
    fireEvent.click(screen.getByRole('option', { name: 'No' }))
    expect(changed).toHaveBeenLastCalledWith(false)
  })

  it('preserves unfinished numeric text and unsafe integers rather than rounding', () => {
    const changed = vi.fn()
    render(<Harness changed={changed} schema={{ kind: 'integer' }} />)
    for (const raw of ['-', '1e', '9007199254740993']) {
      fireEvent.change(screen.getByRole('textbox'), { target: { value: raw } })
      expect(changed).toHaveBeenLastCalledWith(raw)
      expect(screen.getByRole('textbox')).toHaveValue(raw)
    }
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '1e3' } })
    expect(changed).toHaveBeenLastCalledWith(1000)
  })

  it('explicitly disables nested MUI selects while preserving inspection', () => {
    const changed = vi.fn()
    render(<ProfileConstantInput label="Choice" schema={{ kind: 'array', items: { kind: 'enum', values: ['one'] } }}
      value={['one']} nullable={false} disabled onChange={changed} onBlur={vi.fn()} />)
    expect(screen.getByRole('combobox')).toHaveAttribute('aria-disabled', 'true')
    fireEvent.mouseDown(screen.getByRole('combobox'))
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(changed).not.toHaveBeenCalled()
  })

  it('does not hide decimal rounding behind exponent notation or restrict faithful large numbers', () => {
    const changed = vi.fn()
    render(<Harness changed={changed} schema={{ kind: 'number' }} />)
    for (const raw of ['9007199254740993e0', '0.10000000000000001']) {
      fireEvent.change(screen.getByRole('textbox'), { target: { value: raw } })
      expect(changed).toHaveBeenLastCalledWith(raw)
    }
    for (const raw of ['1e100', '.25', '1.00e-3']) {
      fireEvent.change(screen.getByRole('textbox'), { target: { value: raw } })
      expect(changed).toHaveBeenLastCalledWith(Number(raw))
    }
  })
})
