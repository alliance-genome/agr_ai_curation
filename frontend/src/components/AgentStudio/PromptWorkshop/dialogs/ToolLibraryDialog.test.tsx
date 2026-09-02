import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { ToolLibraryItem } from '@/types/promptExplorer'

import ToolLibraryDialog from './ToolLibraryDialog'

const tools: ToolLibraryItem[] = [
  { tool_key: 'search_document', display_name: 'Search Document', description: 'Search document sections', category: 'Document', curator_visible: true, allow_attach: true, allow_execute: true, config: {} },
  { tool_key: 'admin_only_tool', display_name: 'Admin Tool', description: 'writes are not permitted', category: 'Admin', curator_visible: true, allow_attach: false, allow_execute: false, config: {} },
  { tool_key: 'chebi_lookup', display_name: 'ChEBI Lookup', description: 'Chemicals', category: 'External API', curator_visible: true, allow_attach: true, allow_execute: true, config: {} },
]

describe('ToolLibraryDialog', () => {
  it('lists tools with checkboxes, keeps attached tools checked, and counts the attach footer', () => {
    const onConfirm = vi.fn()
    render(<ToolLibraryDialog open tools={tools} attachedToolIds={['search_document']} onConfirm={onConfirm} onClose={vi.fn()} />)
    const dialog = screen.getByRole('dialog', { name: /Add tools/ })
    expect(dialog).toHaveTextContent('1 attached · 2 available')
    expect(within(dialog).getByRole('checkbox', { name: /search_document/ })).toHaveAttribute('aria-checked', 'true')

    const footer = within(dialog).getByRole('button', { name: 'Attach tools' })
    expect(footer).toBeDisabled()

    fireEvent.click(within(dialog).getByRole('checkbox', { name: /chebi_lookup/ }))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Attach 1 tool' }))
    expect(onConfirm).toHaveBeenCalledWith(['search_document', 'chebi_lookup'])
  })

  it('lists policy-disabled tools with the reason but does not select them', () => {
    render(<ToolLibraryDialog open tools={tools} attachedToolIds={[]} onConfirm={vi.fn()} onClose={vi.fn()} />)
    const dialog = screen.getByRole('dialog')
    const blocked = within(dialog).getByRole('checkbox', { name: /admin_only_tool/ })
    expect(blocked).toHaveAttribute('aria-disabled', 'true')
    expect(dialog).toHaveTextContent('Disabled by policy for custom agents: writes are not permitted')
    fireEvent.click(blocked)
    expect(blocked).toHaveAttribute('aria-checked', 'false')
    expect(within(dialog).getByRole('button', { name: 'Attach tools' })).toBeDisabled()
  })

  it('labels the footer for removals', () => {
    const onConfirm = vi.fn()
    render(<ToolLibraryDialog open tools={tools} attachedToolIds={['search_document', 'chebi_lookup']} onConfirm={onConfirm} onClose={vi.fn()} />)
    fireEvent.click(screen.getByRole('checkbox', { name: /chebi_lookup/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Remove 1 tool' }))
    expect(onConfirm).toHaveBeenCalledWith(['search_document'])
  })

  it('filters by search and category', async () => {
    render(<ToolLibraryDialog open tools={tools} attachedToolIds={[]} onConfirm={vi.fn()} onClose={vi.fn()} />)
    const dialog = screen.getByRole('dialog')
    fireEvent.mouseDown(within(dialog).getByRole('combobox', { name: 'Category' }))
    fireEvent.click(await screen.findByRole('option', { name: 'External API' }))
    expect(within(dialog).getByText('chebi_lookup')).toBeInTheDocument()
    expect(within(dialog).queryByText('search_document')).not.toBeInTheDocument()
    expect(within(dialog).queryByText('admin_only_tool')).not.toBeInTheDocument()

    fireEvent.change(within(dialog).getByLabelText('Search tools'), { target: { value: 'nothing here' } })
    expect(within(dialog).getByText('No tools match your search')).toBeInTheDocument()
  })

  it('cancels without confirming', () => {
    const onClose = vi.fn()
    const onConfirm = vi.fn()
    render(<ToolLibraryDialog open tools={tools} attachedToolIds={[]} onConfirm={onConfirm} onClose={onClose} />)
    fireEvent.click(screen.getByRole('checkbox', { name: /chebi_lookup/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(onConfirm).not.toHaveBeenCalled()
  })
})
