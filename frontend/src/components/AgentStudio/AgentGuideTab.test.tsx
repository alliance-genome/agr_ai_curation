import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import AgentGuideTab from './AgentGuideTab'
import type { AgentDocumentation } from '@/types/promptExplorer'

function richDocumentation(overrides: Partial<AgentDocumentation> = {}): AgentDocumentation {
  return {
    summary: 'Confirms disease names against the ontology.',
    capabilities: [
      {
        name: 'Disease name lookup',
        description: 'Find identifiers for disease names using case-insensitive search.',
        example_query: "Look up Alzheimer's disease",
        example_result: 'DOID:10652 with name, definition, and synonyms',
      },
      {
        name: 'Synonym search',
        description: 'Find diseases by synonym when the exact term is not found.',
      },
    ],
    data_sources: [
      {
        name: 'Disease term records',
        description: 'Curated copy of the Disease Ontology.',
        species_supported: ['species_a', 'species_b'],
        data_types: ['ontology'],
      },
      {
        name: 'PDF Document Search',
        description: 'Search over uploaded papers.',
        data_types: ['PDF text chunks'],
      },
    ],
    limitations: ['Only queries Disease Ontology terms.', 'Prevalence statistics are not available.'],
    use_when: ['After any extractor that names a disease.', 'Before curation handoff so identifiers are settled.'],
    avoid_when: ['For gene to disease associations.'],
    note: '',
    ...overrides,
  }
}

const baseProps = {
  tools: ['lookup_term', 'search_synonyms'],
  toolDescriptions: { lookup_term: 'Find a term by name.' },
  onShowToolDetails: vi.fn(),
  onDraftGuide: vi.fn(),
}

describe('AgentGuideTab', () => {
  it('renders stripes, capabilities, limitations, data sources, and tools in order for rich documentation', () => {
    render(<AgentGuideTab {...baseProps} documentation={richDocumentation()} />)

    const useStripe = screen.getByRole('region', { name: 'When to use it' })
    expect(within(useStripe).getAllByRole('listitem')).toHaveLength(2)
    const avoidStripe = screen.getByRole('region', { name: 'When not to use it' })
    expect(avoidStripe).toHaveTextContent('For gene to disease associations.')

    const capabilities = screen.getByRole('region', { name: 'Capabilities' })
    const rows = within(capabilities).getAllByRole('listitem')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent("Look up Alzheimer's disease returns DOID:10652 with name, definition, and synonyms")
    expect(within(rows[0]).getByText("Look up Alzheimer's disease").tagName).toBe('CODE')
    expect(rows[1]).not.toHaveTextContent('returns')

    const limitations = screen.getByRole('region', { name: 'Limitations' })
    expect(within(limitations).getAllByRole('listitem')).toHaveLength(2)

    expect(screen.getByText('2 tools')).toBeInTheDocument()

    const headings = screen.getAllByRole('heading', { level: 3 }).map((heading) => heading.textContent)
    expect(headings).toEqual([
      'When to use it',
      'When not to use it',
      'Capabilities',
      'Limitations',
      'Data sources',
      'Tools',
    ])
    expect(screen.queryByText('What it needs and returns')).not.toBeInTheDocument()
    expect(screen.queryByText('No curator guide yet')).not.toBeInTheDocument()
  })

  it('lists data sources after limitations with a bold name, one description line, and species only when present', () => {
    render(<AgentGuideTab {...baseProps} documentation={richDocumentation()} />)

    const sources = screen.getByRole('region', { name: 'Data sources' })
    const rows = within(sources).getAllByRole('listitem')
    expect(rows).toHaveLength(2)

    const name = within(rows[0]).getByText('Disease term records')
    expect(name).toHaveStyle({ fontWeight: 600 })
    expect(within(rows[0]).getByText('Curated copy of the Disease Ontology.')).toBeInTheDocument()
    expect(within(rows[0]).getByText('Species: species_a, species_b')).toBeInTheDocument()

    expect(within(rows[1]).getByText('PDF Document Search')).toBeInTheDocument()
    expect(rows[1]).not.toHaveTextContent('Species:')

    expect(sources).not.toHaveTextContent('Data types')
    expect(sources).not.toHaveTextContent('ontology')
    expect(sources).not.toHaveTextContent('PDF text chunks')

    const limitations = screen.getByRole('region', { name: 'Limitations' })
    expect(limitations.compareDocumentPosition(sources) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('renders a non-empty note verbatim in a warning alert above the use stripe', () => {
    const note = 'Validation runs automatically. This check runs on every disease term an extractor produces.'
    render(<AgentGuideTab {...baseProps} documentation={richDocumentation({ note })} />)

    const alert = screen.getByTestId('guide-note')
    expect(alert).toHaveAttribute('role', 'alert')
    expect(alert).toHaveClass('MuiAlert-standardWarning')
    expect(alert.textContent).toBe(note)

    const useStripe = screen.getByRole('region', { name: 'When to use it' })
    expect(alert.compareDocumentPosition(useStripe) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('renders no note when the note is empty or whitespace', () => {
    const { unmount } = render(<AgentGuideTab {...baseProps} documentation={richDocumentation({ note: '' })} />)
    expect(screen.queryByTestId('guide-note')).not.toBeInTheDocument()
    unmount()

    render(<AgentGuideTab {...baseProps} documentation={richDocumentation({ note: '   ' })} />)
    expect(screen.queryByTestId('guide-note')).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('omits the stripes when use_when and avoid_when are absent and still lists limitations', () => {
    render(<AgentGuideTab {...baseProps} documentation={richDocumentation({ use_when: [], avoid_when: [] })} />)

    expect(screen.queryByRole('region', { name: 'When to use it' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'When not to use it' })).not.toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Limitations' })).toBeInTheDocument()
  })

  it('renders one stripe alone when only one of the pair is present', () => {
    render(<AgentGuideTab {...baseProps} documentation={richDocumentation({ avoid_when: [] })} />)
    expect(screen.getByRole('region', { name: 'When to use it' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'When not to use it' })).not.toBeInTheDocument()
  })

  it('omits the limitations section when the list is empty', () => {
    render(<AgentGuideTab {...baseProps} documentation={richDocumentation({ limitations: [] })} />)
    expect(screen.queryByRole('region', { name: 'Limitations' })).not.toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Capabilities' })).toBeInTheDocument()
  })

  it('omits the data sources section when the list is empty', () => {
    render(<AgentGuideTab {...baseProps} documentation={richDocumentation({ data_sources: [] })} />)
    expect(screen.queryByRole('region', { name: 'Data sources' })).not.toBeInTheDocument()
  })

  it('shows the honest empty block with a draft action for sparse documentation and keeps tools', () => {
    const onDraftGuide = vi.fn()
    render(
      <AgentGuideTab
        {...baseProps}
        onDraftGuide={onDraftGuide}
        documentation={{ summary: '', capabilities: [], data_sources: [], limitations: [], use_when: [], avoid_when: [], note: '' }}
      />
    )

    expect(screen.getByText('No curator guide yet')).toBeInTheDocument()
    expect(screen.getByText(/Its prompts are on the Prompts tab/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Ask AI Chat to draft a guide' }))
    expect(onDraftGuide).toHaveBeenCalledTimes(1)
    expect(screen.getByText('2 tools')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Capabilities' })).not.toBeInTheDocument()
  })

  it('treats a missing documentation payload as sparse', () => {
    render(<AgentGuideTab {...baseProps} documentation={undefined} tools={[]} />)
    expect(screen.getByText('No curator guide yet')).toBeInTheDocument()
    expect(screen.getByText(/This agent has no tools/)).toBeInTheDocument()
  })

  it('reaches tool details through the tools table', () => {
    const onShowToolDetails = vi.fn()
    render(<AgentGuideTab {...baseProps} onShowToolDetails={onShowToolDetails} documentation={richDocumentation()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Show all tools' }))
    fireEvent.click(screen.getByRole('button', { name: 'Details for search_synonyms' }))
    expect(onShowToolDetails).toHaveBeenCalledWith('search_synonyms')
  })
})
