import { render, screen, waitFor, within } from '@/test/test-utils'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import OutputFieldEditor from './OutputFieldEditor'
import type { FlowDefinition } from '../types'

const mocks = vi.hoisted(() => ({ validate: vi.fn() }))
vi.mock('@/services/agentStudioService', () => ({ validateFlowDraft: mocks.validate }))
const definition = { version: '1.1', nodes: [], edges: [], entry_node_id: 'input' } as FlowDefinition
const binding = { status: 'bound' as const, sources: [{ sourceNodeId: 'stocks', sourceLabel: 'Stocks' }] }
const catalog = { stocks: { schema_fingerprint: 'saved-schema', fields: [
  { ref: 'object.attribute.name', label: 'Stock name', value_type: 'string' },
  { ref: 'object.attribute.source', label: 'Supplier', value_type: 'object' },
] } }

describe('Output field editor', () => {
  beforeEach(() => { mocks.validate.mockReset(); mocks.validate.mockResolvedValue({ projection_fields_by_node: catalog }) })
  it('shows empty tables, selects and orders fields, and writes a fixed plan only on Use these fields', async () => {
    const user = userEvent.setup(); const change = vi.fn()
    render(<OutputFieldEditor format="json" definition={definition} binding={binding} value={null} onChange={change} />)
    await user.click(screen.getByRole('button', { name: 'Choose output fields' }))
    expect(await screen.findByRole('table', { name: 'Available output fields' })).toBeInTheDocument()
    expect(within(screen.getByRole('table', { name: 'Selected output fields' })).getByText('Select fields above to build your output.')).toBeInTheDocument()
    await user.click(screen.getByRole('checkbox', { name: 'Include Stocks: Stock name' }))
    await user.click(screen.getByRole('checkbox', { name: 'Include Stocks: Supplier' }))
    await user.click(screen.getByRole('button', { name: 'Move column 2 up' }))
    expect(change).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Use these fields' }))
    expect(change).toHaveBeenCalledWith(expect.objectContaining({ selection_mode: 'selected_fields', missing_value: null,
      selected_sources: [{ node_id: 'stocks', schema_fingerprint: 'saved-schema' }],
      columns: [expect.objectContaining({ field_ref: 'object.attribute.source', source_node_id: 'stocks' }), expect.objectContaining({ field_ref: 'object.attribute.name' })],
    }))
  })
  it('does not apply on Escape or Cancel, and prevents edits after the flow changes', async () => {
    const user = userEvent.setup(); const change = vi.fn()
    const props = { format: 'csv' as const, definition, binding, value: null, onChange: change }
    const view = render(<OutputFieldEditor {...props} />)
    await user.click(screen.getByRole('button', { name: 'Choose output fields' }))
    await user.click(await screen.findByRole('checkbox', { name: 'Include Stocks: Stock name' }))
    await user.keyboard('{Escape}')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    view.rerender(<OutputFieldEditor {...props} definition={{ ...definition, entry_node_id: 'changed' }} />)
    expect(screen.getByRole('button', { name: 'Use these fields' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(change).not.toHaveBeenCalled()
  })
  it('opens an AI-authored plan whose optional header is omitted', async () => {
    const user = userEvent.setup()
    render(<OutputFieldEditor format="csv" definition={definition} binding={binding} onChange={vi.fn()}
      value={{ selection_mode: 'selected_fields', columns: [{ key: 'Reported stock', field_ref: 'object.attribute.name', source_node_id: 'stocks' }] }} />)
    await user.click(screen.getByRole('button', { name: 'Choose output fields' }))
    expect(await screen.findByRole('textbox', { name: 'Column 1 name' })).toHaveValue('Reported stock')
  })
  it('shows loading and unavailable source feedback without enabling Apply', async () => {
    let resolve!: (value: unknown) => void
    mocks.validate.mockImplementation(() => new Promise((r) => { resolve = r }))
    const user = userEvent.setup()
    render(<OutputFieldEditor format="tsv" definition={definition} binding={binding} value={null} onChange={vi.fn()} />)
    await user.click(screen.getByRole('button', { name: 'Choose output fields' }))
    expect(screen.getByRole('status')).toHaveTextContent('Loading saved source fields')
    resolve({ projection_fields_by_node: {} })
    await waitFor(() => expect(screen.getByText(/No declared fields available/)).toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Use these fields' })).toBeDisabled()
  })
})
