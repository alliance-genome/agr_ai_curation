import { render, screen, fireEvent } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it, vi } from 'vitest'
import theme from '@/theme'
import EntityTagTable from './EntityTagTable'
import type { EntityTag } from './types'

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <ThemeProvider theme={theme}>{children}</ThemeProvider>
)

const makeTags = (): EntityTag[] => [
  {
    tag_id: 'tag-1', entity_name: 'daf-2', entity_type: 'ATP:0000005',
    species: 'NCBITaxon:6239', topic: 'gene expression', db_status: 'validated',
    db_entity_id: 'WBGene00000898', source: 'ai', decision: 'pending',
    evidence: { sentence_text: 'The daf-2 receptor regulates lifespan.', page_number: 3, section_title: 'Results', chunk_ids: ['c1'] },
    notes: null,
  },
  {
    tag_id: 'tag-2', entity_name: 'ins-1', entity_type: 'ATP:0000005',
    species: 'NCBITaxon:6239', topic: 'gene expression', db_status: 'ambiguous',
    db_entity_id: null, source: 'ai', decision: 'pending',
    evidence: { sentence_text: 'ins-1 is an insulin peptide.', page_number: 5, section_title: 'Discussion', chunk_ids: ['c2'] },
    notes: null,
  },
]

describe('EntityTagTable', () => {
  it('renders toolbar with counts', () => {
    render(<EntityTagTable tags={makeTags()} />, { wrapper })
    expect(screen.getByText(/2 entities/)).toBeInTheDocument()
    expect(screen.getByText(/2 pending/)).toBeInTheDocument()
  })

  it('renders all entity rows', () => {
    render(<EntityTagTable tags={makeTags()} />, { wrapper })
    expect(screen.getByText('daf-2')).toBeInTheDocument()
    expect(screen.getByText('ins-1')).toBeInTheDocument()
  })

  it('shows evidence pane empty state initially', () => {
    render(<EntityTagTable tags={makeTags()} />, { wrapper })
    expect(screen.getByText('Select a row to view evidence.')).toBeInTheDocument()
  })

  it('shows evidence when a row is clicked', () => {
    render(<EntityTagTable tags={makeTags()} />, { wrapper })
    fireEvent.click(screen.getByText('daf-2'))
    expect(screen.getByText(/daf-2 receptor regulates lifespan/)).toBeInTheDocument()
  })

  it('dispatches PDF navigation event when Show in PDF is clicked', () => {
    const listener = vi.fn()
    window.addEventListener('pdf-viewer-navigate-evidence', listener)
    render(<EntityTagTable tags={makeTags()} />, { wrapper })
    fireEvent.click(screen.getByText('daf-2'))
    fireEvent.click(screen.getByText('Show in PDF'))
    expect(listener).toHaveBeenCalled()
    window.removeEventListener('pdf-viewer-navigate-evidence', listener)
  })

  it('accepts a tag when Accept is clicked', () => {
    render(<EntityTagTable tags={makeTags()} />, { wrapper })
    const acceptButtons = screen.getAllByRole('button', { name: 'Accept' })
    fireEvent.click(acceptButtons[0])
    expect(screen.getByText('Accepted')).toBeInTheDocument()
  })
})
