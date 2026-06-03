import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ToolDetailsDialog from './ToolDetailsDialog'
import type { ToolInfo } from '@/types/promptExplorer'

const serviceMocks = vi.hoisted(() => ({
  fetchToolDetails: vi.fn(),
}))

vi.mock('@/services/agentStudioService', () => serviceMocks)

function buildTool(): ToolInfo {
  return {
    name: 'Search Document',
    description: 'Searches the document for relevant passages.',
    category: 'Document',
    source_file: 'tools/search_document.py',
    documentation: {
      summary: 'Runs a semantic search across the ingested document text.',
      parameters: [
        {
          name: 'query',
          type: 'string',
          required: true,
          description: 'The text to search for.',
        },
      ],
    },
  }
}

describe('ToolDetailsDialog (slide-over)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    serviceMocks.fetchToolDetails.mockResolvedValue(buildTool())
  })

  it('renders tool details in a slide-over with a working close control', async () => {
    const onClose = vi.fn()

    render(
      <ToolDetailsDialog
        open
        toolId="search_document"
        agentId="gene_extractor"
        onClose={onClose}
      />
    )

    // Header of the slide-over.
    expect(screen.getByText('Tool Details')).toBeInTheDocument()

    // Tool payload is loaded via the mocked service and rendered.
    expect(serviceMocks.fetchToolDetails).toHaveBeenCalledWith('search_document', 'gene_extractor')
    expect(await screen.findByText('Search Document')).toBeInTheDocument()
    expect(screen.getByText('Searches the document for relevant passages.')).toBeInTheDocument()

    // Close controls are present (header icon + footer button) and wired to onClose.
    const closeControls = screen.getAllByRole('button', { name: 'Close' })
    expect(closeControls.length).toBeGreaterThan(0)
    fireEvent.click(closeControls[0])
    expect(onClose).toHaveBeenCalled()
  })
})
