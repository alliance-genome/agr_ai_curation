import { render, screen, fireEvent } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it, vi } from 'vitest'
import theme from '@/theme'
import InlineEditRow from './InlineEditRow'
import type { EntityTag } from './types'

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <ThemeProvider theme={theme}>
    <table><tbody>{children}</tbody></table>
  </ThemeProvider>
)

const makeTag = (): EntityTag => ({
  tag_id: 'tag-1', entity_name: 'daf-2', entity_type: 'ATP:0000005',
  species: 'NCBITaxon:6239', topic: 'gene expression', db_status: 'validated',
  db_entity_id: 'WBGene00000898', source: 'ai', decision: 'pending',
  evidence: { sentence_text: 'daf-2 regulates lifespan.', page_number: 3, section_title: 'Results', chunk_ids: ['c1'] },
  notes: null,
})

describe('InlineEditRow', () => {
  it('renders input fields pre-filled with tag values', () => {
    render(<InlineEditRow tag={makeTag()} onSave={vi.fn()} onCancel={vi.fn()} />, { wrapper })
    const entityInput = screen.getByDisplayValue('daf-2')
    expect(entityInput).toBeInTheDocument()
    expect(screen.getByDisplayValue('gene expression')).toBeInTheDocument()
  })

  it('renders entity type as a select dropdown', () => {
    render(<InlineEditRow tag={makeTag()} onSave={vi.fn()} onCancel={vi.fn()} />, { wrapper })
    expect(screen.getByRole('combobox', { name: 'Entity type' })).toBeInTheDocument()
  })

  it('calls onSave with updated values when Save is clicked', () => {
    const onSave = vi.fn()
    render(<InlineEditRow tag={makeTag()} onSave={onSave} onCancel={vi.fn()} />, { wrapper })
    const entityInput = screen.getByDisplayValue('daf-2')
    fireEvent.change(entityInput, { target: { value: 'daf-16' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(onSave).toHaveBeenCalledWith('tag-1', expect.objectContaining({ entity_name: 'daf-16' }))
  })

  it('calls onCancel when Cancel is clicked', () => {
    const onCancel = vi.fn()
    render(<InlineEditRow tag={makeTag()} onSave={vi.fn()} onCancel={onCancel} />, { wrapper })
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onCancel).toHaveBeenCalled()
  })
})
