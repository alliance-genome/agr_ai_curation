import { describe, expect, it, vi } from 'vitest'

import { render, screen, userEvent } from '@/test/test-utils'
import type { EvidenceRecord } from '@/features/curation/types'

import TranscriptMessage, { type TranscriptMessageRecord } from './TranscriptMessage'

const EVIDENCE_RECORDS: EvidenceRecord[] = [
  {
    entity: 'TP53',
    verified_quote: 'TP53 increased in the treated samples.',
    page: 2,
    section: 'Results',
    chunk_id: 'chunk-1',
  },
  {
    entity: 'BRCA1',
    verified_quote: 'BRCA1 remained stable across the control group.',
    page: 4,
    section: 'Discussion',
    subsection: 'Controls',
    chunk_id: 'chunk-2',
  },
]

function renderTranscriptMessage(overrides: Partial<TranscriptMessageRecord> = {}) {
  const message: TranscriptMessageRecord = {
    role: 'assistant',
    content: 'Stored assistant answer',
    ...overrides,
  }

  return render(<TranscriptMessage message={message} />)
}

describe('TranscriptMessage', () => {
  it('renders stored user rows as read-only transcript bubbles', () => {
    renderTranscriptMessage({
      role: 'user',
      content: 'Please summarize the findings.',
    })

    expect(screen.getByTestId('transcript-message-user')).toBeInTheDocument()
    expect(screen.getByText('You')).toBeInTheDocument()
    expect(screen.getByText('Please summarize the findings.')).toBeInTheDocument()
    expect(screen.queryAllByRole('button')).toHaveLength(0)
  })

  it('renders stored assistant rows with transcript-safe evidence previews', async () => {
    const user = userEvent.setup()

    renderTranscriptMessage({
      content: 'Stored assistant answer',
      evidenceRecords: EVIDENCE_RECORDS,
    })

    expect(screen.getByTestId('transcript-message-assistant')).toBeInTheDocument()
    expect(screen.getByText('AI Assistant')).toBeInTheDocument()
    expect(screen.getByText('Stored assistant answer')).toBeInTheDocument()
    expect(screen.getByText('2 evidence quotes')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /review & curate/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /highlight evidence on pdf/i })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'TP53 1' }))

    expect(screen.getByText('p. 2 · Results')).toBeInTheDocument()
    expect(screen.getByText('“TP53 increased in the treated samples.”')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /highlight evidence on pdf/i })).not.toBeInTheDocument()
  })

  it('renders stored assistant file rows without live download controls', () => {
    renderTranscriptMessage({
      type: 'file_download',
      content: '',
      fileData: {
        file_id: 'file-1',
        filename: 'gene-results.csv',
        format: 'csv',
        size_bytes: 2048,
        download_url: '/api/files/file-1/download',
      },
    })

    expect(screen.getByTestId('transcript-file-card')).toBeInTheDocument()
    expect(screen.getByText('gene-results.csv')).toBeInTheDocument()
    expect(screen.getByText('CSV')).toBeInTheDocument()
    expect(screen.getByText('2.0 KB')).toBeInTheDocument()
    expect(screen.queryAllByRole('button')).toHaveLength(0)
  })

  it('renders unknown stored transcript file formats without throwing', () => {
    renderTranscriptMessage({
      type: 'file_download',
      content: '',
      fileData: {
        file_id: 'file-2',
        filename: 'gene-results.xml',
        format: 'xml',
        size_bytes: 128,
        download_url: '/api/files/file-2/download',
      },
    })

    expect(screen.getByTestId('transcript-file-card')).toBeInTheDocument()
    expect(screen.getByText('gene-results.xml')).toBeInTheDocument()
    expect(screen.getByText('XML')).toBeInTheDocument()
    expect(screen.getByText('128 B')).toBeInTheDocument()
    expect(screen.queryAllByRole('button')).toHaveLength(0)
  })

  it('renders transcript-only flow evidence rows without assistant controls', async () => {
    const user = userEvent.setup()

    renderTranscriptMessage({
      role: 'flow',
      content: '',
      flowStepEvidence: {
        flow_id: 'flow-1',
        flow_name: 'Flow Evidence',
        flow_run_id: 'run-1',
        step: 2,
        tool_name: 'ask_gene_specialist',
        agent_id: 'gene',
        agent_name: 'Gene Agent',
        evidence_records: [EVIDENCE_RECORDS[0]],
        evidence_count: 3,
        total_evidence_records: 7,
      },
    })

    expect(screen.getByTestId('transcript-flow-step-evidence-card')).toBeInTheDocument()
    expect(screen.getByText('Step 2 / Gene Agent / ask_gene_specialist')).toBeInTheDocument()
    expect(
      screen.getByText('Showing 1 evidence quote preview from 3 evidence quotes captured in this step.'),
    ).toBeInTheDocument()
    expect(screen.getByText('7 evidence quotes collected so far in this run.')).toBeInTheDocument()
    expect(screen.getByText('1 evidence quote preview')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /highlight evidence on pdf/i })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'TP53 1' }))

    expect(screen.getByText('p. 2 · Results')).toBeInTheDocument()
    expect(screen.getByText('“TP53 increased in the treated samples.”')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /review & curate/i })).not.toBeInTheDocument()
  })

  it('throws for unsupported transcript message roles', () => {
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

    try {
      expect(() =>
        renderTranscriptMessage({
          role: 'system' as TranscriptMessageRecord['role'],
          content: 'Unexpected role payload',
        }),
      ).toThrow('Unhandled transcript message role: system')
    } finally {
      consoleErrorSpy.mockRestore()
    }
  })
})
