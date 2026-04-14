import { render, screen, fireEvent } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it, vi } from 'vitest'
import theme from '@/theme'
import EntityTagRow from './EntityTagRow'
import type { EntityTag } from './types'

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <ThemeProvider theme={theme}>
    <table><tbody>{children}</tbody></table>
  </ThemeProvider>
)

const makeTag = (overrides: Partial<EntityTag> = {}): EntityTag => ({
  tag_id: 'tag-1', entity_name: 'daf-2', entity_type: 'ATP:0000005',
  species: 'NCBITaxon:6239', topic: 'gene expression', db_status: 'validated',
  db_entity_id: 'WBGene00000898', source: 'ai', decision: 'pending',
  evidence: { sentence_text: 'The daf-2 receptor regulates lifespan.', page_number: 3, section_title: 'Results', chunk_ids: ['c1'] },
  notes: null,
  ...overrides,
})

const defaultProps = {
  tag: makeTag(),
  isSelected: false,
  onSelect: vi.fn(),
  onAccept: vi.fn(),
  onReject: vi.fn(),
  onEdit: vi.fn(),
  onDelete: vi.fn(),
}

describe('EntityTagRow', () => {
  it('renders entity name, type, species, topic', () => {
    render(<EntityTagRow {...defaultProps} />, { wrapper })
    expect(screen.getByText('daf-2')).toBeInTheDocument()
    expect(screen.getByText('gene')).toBeInTheDocument()
    expect(screen.getByText('gene expression')).toBeInTheDocument()
  })

  it('shows Accept and Reject buttons for pending tags', () => {
    render(<EntityTagRow {...defaultProps} />, { wrapper })
    expect(screen.getByRole('button', { name: 'Accept' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reject' })).toBeInTheDocument()
  })

  it('shows Accepted badge for accepted tags', () => {
    render(<EntityTagRow {...defaultProps} tag={makeTag({ decision: 'accepted' })} />, { wrapper })
    expect(screen.getByText('Accepted')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Accept' })).not.toBeInTheDocument()
  })

  it('shows Rejected badge for rejected tags', () => {
    render(<EntityTagRow {...defaultProps} tag={makeTag({ decision: 'rejected' })} />, { wrapper })
    expect(screen.getByText('Rejected')).toBeInTheDocument()
  })

  it('shows validated badge for db_status validated', () => {
    render(<EntityTagRow {...defaultProps} />, { wrapper })
    expect(screen.getByText('validated')).toBeInTheDocument()
  })

  it('calls onSelect when row is clicked', () => {
    const onSelect = vi.fn()
    render(<EntityTagRow {...defaultProps} onSelect={onSelect} />, { wrapper })
    fireEvent.click(screen.getByText('daf-2'))
    expect(onSelect).toHaveBeenCalledWith('tag-1')
  })

  it('calls onAccept when Accept button is clicked', () => {
    const onAccept = vi.fn()
    render(<EntityTagRow {...defaultProps} onAccept={onAccept} />, { wrapper })
    fireEvent.click(screen.getByRole('button', { name: 'Accept' }))
    expect(onAccept).toHaveBeenCalledWith('tag-1')
  })

  it('shows edit icon that calls onEdit', () => {
    const onEdit = vi.fn()
    render(<EntityTagRow {...defaultProps} onEdit={onEdit} />, { wrapper })
    fireEvent.click(screen.getByLabelText('Edit daf-2'))
    expect(onEdit).toHaveBeenCalledWith('tag-1')
  })

  it('shows delete icon that calls onDelete', () => {
    const onDelete = vi.fn()
    render(<EntityTagRow {...defaultProps} onDelete={onDelete} />, { wrapper })
    fireEvent.click(screen.getByLabelText('Delete daf-2'))
    expect(onDelete).toHaveBeenCalledWith('tag-1')
  })

  it('shows source label', () => {
    render(<EntityTagRow {...defaultProps} />, { wrapper })
    expect(screen.getByText('AI')).toBeInTheDocument()
  })

  it('renders canonical entity type labels from the workspace payload', () => {
    render(
      <EntityTagRow
        {...defaultProps}
        tag={makeTag({ entity_type: 'gene' })}
      />,
      { wrapper },
    )

    expect(screen.getByText('gene')).toBeInTheDocument()
  })

  it('fails loudly for unknown entity type identifiers', () => {
    expect(() =>
      render(
        <EntityTagRow
          {...defaultProps}
          tag={makeTag({ entity_type: 'CUSTOM:entity_type' })}
        />,
        { wrapper },
      ),
    ).toThrow(/Unknown entity type code/i)
  })
})
