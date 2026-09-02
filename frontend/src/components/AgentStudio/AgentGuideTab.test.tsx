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
    ],
    limitations: ['Only queries Disease Ontology terms.', 'Prevalence statistics are not available.'],
    use_when: ['After any extractor that names a disease.', 'Before curation handoff so identifiers are settled.'],
    avoid_when: ['For gene to disease associations.'],
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
  it('renders stripes, reads, capabilities, limitations, and tools in order for rich documentation', () => {
    render(<AgentGuideTab {...baseProps} documentation={richDocumentation()} />)

    const useStripe = screen.getByRole('region', { name: 'When to use it' })
    expect(within(useStripe).getAllByRole('listitem')).toHaveLength(2)
    const avoidStripe = screen.getByRole('region', { name: 'When not to use it' })
    expect(avoidStripe).toHaveTextContent('For gene to disease associations.')

    const reads = screen.getByRole('region', { name: 'What it needs and returns' })
    expect(within(reads).getByText('Reads')).toBeInTheDocument()
    expect(reads).toHaveTextContent('Disease term records: Curated copy of the Disease Ontology. Species: species_a, species_b. Data types: ontology.')

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
      'What it needs and returns',
      'Capabilities',
      'Limitations',
      'Tools',
    ])
    expect(screen.queryByText('No curator guide yet')).not.toBeInTheDocument()
  })

  it('shows the automatic validation note above the stripes for a Validation agent', () => {
    render(<AgentGuideTab {...baseProps} category="Validation" documentation={richDocumentation()} />)

    const note = screen.getByTestId('automatic-validation-note')
    expect(note).toHaveAttribute('role', 'alert')
    expect(note).toHaveClass('MuiAlert-standardWarning')
    expect(within(note).getByText('Validation runs automatically.').tagName).toBe('STRONG')
    expect(note).toHaveTextContent(
      'Every validator in the domain pack runs automatically on the objects an extractor produces. '
      + 'Add this validator to a flow only when you want custom validation: a changed prompt, different tools, '
      + 'or a check the pack does not include. Clone it in the Agent Workshop to customize it.'
    )
    const useStripe = screen.getByRole('region', { name: 'When to use it' })
    expect(note.compareDocumentPosition(useStripe) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('shows the extractor wording for an Extraction agent, matching the category case-insensitively', () => {
    render(<AgentGuideTab {...baseProps} category="extraction" documentation={richDocumentation()} />)

    const note = screen.getByTestId('automatic-validation-note')
    expect(within(note).getByText('Validation runs automatically.')).toBeInTheDocument()
    expect(note).toHaveTextContent(
      "The domain pack's validators run automatically on everything this agent extracts. "
      + 'You do not need to add validators to the flow unless you want custom validation.'
    )
    expect(note).not.toHaveTextContent('Clone it in the Agent Workshop')
  })

  it('omits the note for other categories and when no category is known', () => {
    const { unmount } = render(<AgentGuideTab {...baseProps} category="Output" documentation={richDocumentation()} />)
    expect(screen.queryByTestId('automatic-validation-note')).not.toBeInTheDocument()
    unmount()

    render(<AgentGuideTab {...baseProps} documentation={richDocumentation()} />)
    expect(screen.queryByTestId('automatic-validation-note')).not.toBeInTheDocument()
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

  it('shows the honest empty block with a draft action for sparse documentation and keeps tools', () => {
    const onDraftGuide = vi.fn()
    render(
      <AgentGuideTab
        {...baseProps}
        onDraftGuide={onDraftGuide}
        documentation={{ summary: '', capabilities: [], data_sources: [], limitations: [], use_when: [], avoid_when: [] }}
      />
    )

    expect(screen.getByText('No curator guide yet')).toBeInTheDocument()
    expect(screen.getByText(/Its prompts are on the Prompts tab/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Ask Claude to draft a guide' }))
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
