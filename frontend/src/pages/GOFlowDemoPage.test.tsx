import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { ThemeProvider } from '@mui/material/styles'
import React from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { CurationWorkspace } from '@/features/curation/types'
import theme from '@/theme'
import GOFlowDemoPage from './GOFlowDemoPage'

const serviceMocks = vi.hoisted(() => ({
  fetchCurationWorkspace: vi.fn(),
}))

vi.mock('@/features/curation/services/curationWorkspaceService', () => ({
  fetchCurationWorkspace: serviceMocks.fetchCurationWorkspace,
}))

vi.mock('reactflow', () => ({
  __esModule: true,
  default: ({
    children,
    edges,
    nodes,
    onEdgeClick,
    onNodeClick,
  }: {
    children?: React.ReactNode
    edges: Array<{ id: string; data: { relation: { predicate: { label: string } } } }>
    nodes: Array<{ id: string; data: { activity: { title: string } } }>
    onEdgeClick?: (event: unknown, edge: { id: string }) => void
    onNodeClick?: (event: unknown, node: { id: string }) => void
  }) => (
    <div data-testid="react-flow">
      {nodes.map((node) => (
        <button
          key={node.id}
          type="button"
          onClick={() => onNodeClick?.({}, node)}
        >
          {node.data.activity.title}
        </button>
      ))}
      {edges.map((edge) => (
        <button
          key={edge.id}
          type="button"
          onClick={() => onEdgeClick?.({}, edge)}
        >
          {edge.data.relation.predicate.label}
        </button>
      ))}
      {children}
    </div>
  ),
  Background: () => <div data-testid="react-flow-background" />,
  BackgroundVariant: {
    Dots: 'dots',
  },
  Controls: () => <div data-testid="react-flow-controls" />,
  Handle: () => null,
  MarkerType: {
    ArrowClosed: 'arrowclosed',
  },
  MiniMap: () => <div data-testid="react-flow-minimap" />,
  Position: {
    Left: 'left',
    Right: 'right',
  },
  ReactFlowProvider: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))

function buildWorkspace(): CurationWorkspace {
  return {
    session: {
      session_id: 'session-1',
      status: 'in_progress',
      adapter: {
        adapter_key: 'gene_ontology',
        display_label: 'Gene Ontology',
        color_token: 'green',
        metadata: {},
      },
      document: {
        document_id: 'document-1',
        title: 'Live workspace paper',
        pmid: '20369020',
        doi: '10.1371/journal.pgen.1000892',
        pdf_url: '/api/documents/document-1.pdf',
        viewer_url: '/api/documents/document-1.pdf',
        page_count: 12,
      },
      progress: {
        total_candidates: 3,
        reviewed_candidates: 1,
        pending_candidates: 2,
        accepted_candidates: 1,
        rejected_candidates: 0,
        manual_candidates: 0,
      },
      validation: {
        state: 'completed',
        counts: {
          validated: 5,
          ambiguous: 1,
          not_found: 0,
          invalid_format: 0,
          conflict: 0,
          skipped: 0,
          overridden: 0,
        },
        stale_field_keys: [],
        warnings: ['One field needs curator review'],
      },
      evidence: {
        total_anchor_count: 4,
        resolved_anchor_count: 3,
        viewer_highlightable_anchor_count: 3,
        quality_counts: {
          exact_quote: 2,
          normalized_quote: 1,
          section_only: 1,
          page_only: 0,
          document_only: 0,
          unresolved: 0,
        },
        degraded: false,
        warnings: [],
      },
      current_candidate_id: null,
      prepared_at: '2026-05-20T12:00:00Z',
      warnings: [],
      tags: [],
      session_version: 1,
      extraction_results: [],
    },
    entity_tags: [],
    candidates: [],
    action_log: [],
    submission_history: [],
  }
}

function renderPage(path: string, state?: unknown) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
    },
  })

  return render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider theme={theme}>
        <MemoryRouter initialEntries={[state === undefined ? path : { pathname: path, state }]}>
          <Routes>
            <Route path="/go-flow-demo" element={<GOFlowDemoPage />} />
            <Route path="/go-flow-demo/:sessionId" element={<GOFlowDemoPage />} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  )
}

describe('GOFlowDemoPage', () => {
  beforeEach(() => {
    serviceMocks.fetchCurationWorkspace.mockReset()
  })

  it('renders the static Shivers graph without a workspace session', () => {
    renderPage('/go-flow-demo')

    expect(screen.getByRole('heading', { name: 'Draft GO activity model' })).toBeInTheDocument()
    expect(screen.getAllByText(/Shivers et al\. 2010/).length).toBeGreaterThan(0)
    expect(screen.getByText('Static demo graph')).toBeInTheDocument()
    expect(screen.getByText('Read-only mockup')).toBeInTheDocument()
    expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'PMK-1 MAP kinase activity' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'ATF-7 DNA-binding transcription factor activity' })).toBeInTheDocument()
    expect(serviceMocks.fetchCurationWorkspace).not.toHaveBeenCalled()
  })

  it('updates details when a node or edge is selected', () => {
    renderPage('/go-flow-demo')

    fireEvent.click(screen.getByRole('button', { name: 'ATF-7 DNA-binding transcription factor activity' }))
    const details = screen.getByRole('complementary', { name: 'GO flow details' })
    expect(within(details).getByText(/WB:WBGene00000223/)).toBeInTheDocument()
    expect(within(details).getByText(/GO:0000981/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'directly positively regulates' }))
    expect(within(details).getByText(/RO:0002629/)).toBeInTheDocument()
    expect(within(details).getByText(/Phosphorylation is kept in this evidence detail/)).toBeInTheDocument()
  })

  it('renders live workspace metadata when a session id is supplied', async () => {
    serviceMocks.fetchCurationWorkspace.mockResolvedValue(buildWorkspace())

    renderPage('/go-flow-demo/session-1', {
      backToWorkspacePath: '/curation/session-1?panel=evidence',
    })

    await waitFor(() => {
      expect(serviceMocks.fetchCurationWorkspace).toHaveBeenCalledWith('session-1')
    })

    expect(await screen.findByText('Live workspace paper')).toBeInTheDocument()
    expect(screen.getByText('PMID:20369020')).toBeInTheDocument()
    expect(screen.getByText('DOI:10.1371/journal.pgen.1000892')).toBeInTheDocument()
    expect(screen.getByText('3 candidates')).toBeInTheDocument()
    expect(screen.getByText('5 validated fields')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /back to workspace/i })).toHaveAttribute(
      'href',
      '/curation/session-1?panel=evidence',
    )
  })

  it('keeps the static graph visible when workspace metadata fails to load', async () => {
    serviceMocks.fetchCurationWorkspace.mockRejectedValue(new Error('workspace unavailable'))

    renderPage('/go-flow-demo/session-1')

    expect(await screen.findByText(/Workspace metadata unavailable: workspace unavailable/)).toBeInTheDocument()
    expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /back to workspace/i })).toHaveAttribute(
      'href',
      '/curation/session-1',
    )
  })
})
