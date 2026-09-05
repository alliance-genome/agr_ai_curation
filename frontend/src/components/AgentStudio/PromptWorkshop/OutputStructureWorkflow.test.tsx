import { useState } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { GenericProfileContract } from '@/services/genericProfileService'
import OutputStructureWorkflow from './OutputStructureWorkflow'

const stock: GenericProfileContract = { name: 'Stocks', semantic_class: 'stock', fields: [
  { key: 'supplier', display_name: 'Supplier', value_schema: { kind: 'object', fields: [
    { key: 'number', display_name: 'Stock number', required: true, value_schema: { kind: 'string' } },
  ] } },
] }

describe('Shared output structure walkthrough', () => {
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
        { path: 'fields[0]', code: 'invalid', message: 'Clarify the stock field' },
      ])} />
    }
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: 'Review Stocks' }))
    expect(screen.getByRole('button', { name: 'Finish Stocks' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Back to details' }))
    expect(screen.getByRole('button', { name: 'Clarify the stock field' })).toBeVisible()
  })
})
