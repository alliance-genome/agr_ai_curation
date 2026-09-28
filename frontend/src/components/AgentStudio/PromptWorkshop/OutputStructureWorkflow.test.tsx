import { useState } from 'react'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { GenericProfileContract } from '@/services/genericProfileService'
import OutputStructureWorkflow from './OutputStructureWorkflow'

const stock: GenericProfileContract = { name: 'Stocks', semantic_class: 'stock', fields: [
  { key: 'supplier', display_name: 'Supplier', value_schema: { kind: 'object', fields: [
    { key: 'number', display_name: 'Stock number', required: true, value_schema: { kind: 'string' } },
  ] } },
] }

describe('Shared output structure walkthrough', () => {
  it('reviews edited nested labels and preserves removals when reopening the draft', () => {
    const saved = structuredClone(stock)
    const validate = vi.fn()
    function Harness() {
      const [value, setValue] = useState(saved)
      return <><OutputStructureWorkflow value={value} onChange={setValue} onValidate={validate} issues={[]} />
        <output aria-label="Current draft">{JSON.stringify(value)}</output></>
    }
    render(<Harness />)
    const openLabels = () => {
      fireEvent.click(screen.getByRole('button', { name: 'Edit Stock number' }))
      fireEvent.click(screen.getByRole('button', { name: 'More field options' }))
      return screen.getByLabelText('Synonyms / source labels (not output fields)')
    }
    fireEvent.change(openLabels(), { target: { value: 'Catalog number\nStock ID' } })
    fireEvent.click(screen.getByRole('button', { name: 'Review Stocks' }))
    expect(validate).toHaveBeenCalledOnce()
    expect(within(screen.getByRole('table', { name: 'Extraction plan' })).getByRole('rowheader', { name: /Stock number/ }))
      .toHaveTextContent('Synonyms / source labels (not output fields): Catalog number · Stock ID')
    fireEvent.click(screen.getByRole('button', { name: 'Back to details' }))
    expect(openLabels()).toHaveValue('Catalog number\nStock ID')
    fireEvent.change(screen.getByLabelText('Synonyms / source labels (not output fields)'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'Review Stocks' }))
    expect(within(screen.getByRole('table', { name: 'Extraction plan' })).getByRole('rowheader', { name: /Stock number/ }))
      .toHaveTextContent('Synonyms / source labels (not output fields): None')
    fireEvent.click(screen.getByRole('button', { name: 'Finish Stocks' }))
    fireEvent.click(screen.getByRole('button', { name: 'Edit Stocks' }))
    expect(openLabels()).toHaveValue('')
    const draft = JSON.parse(screen.getByLabelText('Current draft').textContent!)
    expect(draft.fields[0]).toEqual({ ...stock.fields[0], value_schema: { kind: 'object', fields: [
      { key: 'number', display_name: 'Stock number', required: true, value_schema: { kind: 'string' }, source_labels: [] },
    ] } })
    expect(saved).toEqual(stock)
  })
  it('reviews grouped parts, finishes locally and reopens the same plan', () => {
    const changed = vi.fn(); const validate = vi.fn()
    render(<OutputStructureWorkflow value={stock} onChange={changed} onValidate={validate} issues={[]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Review Stocks' }))
    expect(validate).toHaveBeenCalledOnce()
    expect(screen.getByRole('table', { name: 'Extraction plan' })).toHaveTextContent('Stock number')
    expect(screen.getByText('With its parent answer')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Finish Stocks' }))
    expect(screen.getByRole('heading', { name: 'Your extraction plan' })).toBeVisible()
    expect(screen.getByText(/Use Workshop Save to save your agent/)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Edit Stocks' }))
    expect(screen.getByRole('table', { name: 'Details to collect' })).toHaveTextContent('Stock number')
    expect(changed).not.toHaveBeenCalled()
  })
  it('keeps server findings visible and prevents finishing while checks fail', () => {
    function Harness() {
      const [issues, setIssues] = useState<Parameters<typeof OutputStructureWorkflow>[0]['issues']>([])
      return <OutputStructureWorkflow value={stock} onChange={vi.fn()} issues={issues} onValidate={() => setIssues([
        { path: 'fields[0].source_labels', code: 'invalid', message: 'Source label identifies another canonical field' },
      ])} />
    }
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: 'Review Stocks' }))
    expect(screen.getByRole('button', { name: 'Finish Stocks' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Back to details' }))
    expect(screen.getByRole('button', { name: 'Source label identifies another canonical field' })).toBeVisible()
  })
})
