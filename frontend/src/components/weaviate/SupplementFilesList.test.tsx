import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import supplementAddMetadataFixture from '@/test/fixtures/supplements/supplement_add_metadata.json'
import supplementWaitMetadataFixture from '@/test/fixtures/supplements/supplement_wait_metadata.json'

import {
  WAITING_FOR_SUPPLEMENTS_STAGE,
  parseSupplementJobProgress,
  type SupplementJobProgress,
} from '@/features/documents/supplementProgress'

import SupplementFilesList from './SupplementFilesList'
import { formatSupplementWaitEndTime } from './paperFileReasonText'

describe('SupplementFilesList', () => {
  it('renders nothing when progress or metadata has no supplement entries', () => {
    for (const props of [
      {},
      { progress: null },
      {
        progress: {
          supplements: [],
          supplement_wait_ends_at: '2026-11-24T15:30:00+00:00',
          job_kind: 'supplement_wait' as const,
          current_stage: WAITING_FOR_SUPPLEMENTS_STAGE,
        },
      },
      { metadata: null },
      { metadata: {} },
      { metadata: { supplements: [] } },
      { metadata: { supplements: 'not-a-list' } },
    ]) {
      const { container } = render(<SupplementFilesList {...props} heading="Adding files" />)
      expect(container).toBeEmptyDOMElement()
    }
  })

  it('shows N7 "Waiting for the main paper" for queued entries before the main paper is ready', () => {
    render(
      <SupplementFilesList
        metadata={{
          supplements: [
            {
              provider_file_id: '1',
              display_name: 'Queued_S1.xlsx',
              state: 'queued',
              reason_code: null,
              observed_at: null,
              file_id: null,
              measured_value: null,
              limit_value: null,
            },
          ],
        }}
        currentStage="extracting"
        providerShortLabel="ABC"
      />,
    )

    const list = screen.getByRole('list', { name: 'Supplement files' })
    const row = within(list).getByRole('listitem')
    expect(row).toHaveTextContent('Queued_S1.xlsx')
    expect(row).toHaveTextContent('Waiting for the main paper')
    expect(row).not.toHaveTextContent('Waiting to be added')
  })

  it('shows "Waiting to be added" for converted queued files alongside "Waiting for ABC" during waiting_for_supplements', () => {
    const progress = parseSupplementJobProgress(supplementWaitMetadataFixture.mixed_waiting, {
      currentStage: WAITING_FOR_SUPPLEMENTS_STAGE,
    })

    render(
      <SupplementFilesList
        progress={progress}
        providerShortLabel="ABC"
        heading="Adding files"
      />,
    )

    expect(screen.getByText('Adding files')).toBeInTheDocument()
    const expectedTime = formatSupplementWaitEndTime(
      supplementWaitMetadataFixture.mixed_waiting.supplement_wait_ends_at,
    )
    expect(screen.getByText(`ABC has until ${expectedTime} to convert them.`)).toBeInTheDocument()

    const rows = within(screen.getByRole('list', { name: 'Supplement files' })).getAllByRole('listitem')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('Converted_S1.xlsx')
    expect(rows[0]).toHaveTextContent('Waiting to be added')
    expect(rows[0]).not.toHaveTextContent('Waiting for the main paper')
    expect(rows[1]).toHaveTextContent('Pending_S2.docx')
    expect(rows[1]).toHaveTextContent('Waiting for ABC')
  })

  it('renders all supplement_wait states and outcomes from the real backend fixture', () => {
    const progress = parseSupplementJobProgress(
      supplementWaitMetadataFixture.all_states_and_outcomes,
      { currentStage: WAITING_FOR_SUPPLEMENTS_STAGE },
    )

    render(<SupplementFilesList progress={progress} providerShortLabel="ABC" />)

    const rows = within(screen.getByRole('list', { name: 'Supplement files' })).getAllByRole('listitem')
    expect(rows).toHaveLength(9)
    expect(rows[0]).toHaveTextContent('Queued_S1.xlsx')
    expect(rows[0]).toHaveTextContent('Waiting to be added')
    expect(rows[1]).toHaveTextContent('Waiting_S2.docx')
    expect(rows[1]).toHaveTextContent('Waiting for ABC')
    expect(rows[2]).toHaveTextContent('Adding_S3.csv')
    expect(rows[2]).toHaveTextContent('Being added')
    expect(rows[3]).toHaveTextContent('Added_S4.pdf')
    expect(rows[3]).toHaveTextContent('Added')
    expect(rows[4]).toHaveTextContent('Not added: the import was cancelled.')
    expect(rows[5]).toHaveTextContent('Not added: adding this paper\'s supplements failed.')
    expect(rows[6]).toHaveTextContent(
      'ABC has not converted this file. You can add it later from the Library once ABC shows it as converted.',
    )
    expect(rows[7]).toHaveTextContent('ABC tried and could not convert this file.')
    expect(rows[8]).toHaveTextContent(
      'ABC had not converted this file when the import stopped waiting. You can add it later from the Library once ABC shows it as converted.',
    )
  })

  it('renders paper_busy_when_ready outcomes captured from record_supplement_outcomes_on_finished_job', () => {
    render(
      <SupplementFilesList
        metadata={supplementWaitMetadataFixture.paper_busy_when_ready}
        providerShortLabel="ABC"
      />,
    )

    const row = within(screen.getByRole('list', { name: 'Supplement files' })).getByRole('listitem')
    expect(row).toHaveTextContent(
      'Not added: the paper was being changed when its main paper became ready.',
    )
  })

  it('omits N10 wait-end line once no supplement is waiting', () => {
    const progress = parseSupplementJobProgress(
      supplementWaitMetadataFixture.finished_with_wait_timestamp,
    )

    render(<SupplementFilesList progress={progress} providerShortLabel="ABC" compact />)

    expect(screen.queryByText(/has until/)).not.toBeInTheDocument()
    expect(screen.getByText('Done_S1.xlsx')).toBeInTheDocument()
    expect(screen.getByText('Added')).toBeInTheDocument()
    expect(screen.getByText('Failed_S2.xlsx')).toBeInTheDocument()
    expect(screen.getByText('Not added: adding this paper\'s supplements failed.')).toBeInTheDocument()
  })

  it('renders approved supplement_add outcomes from the real backend fixture', () => {
    render(
      <SupplementFilesList
        metadata={supplementAddMetadataFixture}
        providerShortLabel="ABC"
      />,
    )

    const rows = within(screen.getByRole('list', { name: 'Supplement files' })).getAllByRole('listitem')
    expect(rows).toHaveLength(10)
    expect(rows[0]).toHaveTextContent('Table_S1.xlsx')
    expect(rows[0]).toHaveTextContent('Added')
    expect(rows[1]).toHaveTextContent('Unnamed file')
    expect(rows[1]).toHaveTextContent('Not added: ABC no longer lists this file for the paper.')
    expect(rows[2]).toHaveTextContent('Not added: PDF supplements could not be added when this import ran.')
    expect(rows[3]).toHaveTextContent('This PDF is larger than 20 MB, so it was not added.')
    expect(rows[4]).toHaveTextContent('This PDF could not be opened, so it was not added.')
    expect(rows[5]).toHaveTextContent('You can read this file\'s text in ABC but not its PDF, so it was not added.')
    expect(rows[6]).toHaveTextContent('Not added: the paper already has the most files allowed (20).')
    expect(rows[7]).toHaveTextContent('Not added: the paper would hold more text than AI Curation reads (2 MB).')
    expect(rows[8]).toHaveTextContent('Already in this paper')
    expect(rows[9]).toHaveTextContent('A table in its converted text has rows that do not match its header')
  })

  it('preserves unrecognized-state rows and renders whitespace-only display names as Unnamed file', () => {
    const progress: SupplementJobProgress = {
      job_kind: 'supplement_add',
      current_stage: null,
      supplement_wait_ends_at: null,
      supplements: [
        {
          recognized: false,
          provider_file_id: '77',
          display_name: 'Mystery_S7.xlsx',
        },
        {
          recognized: true,
          provider_file_id: '78',
          display_name: '   ',
          state: 'left_out',
          reason_code: 'future_unrecognized_reason',
          observed_at: null,
          file_id: null,
          measured_value: null,
          limit_value: null,
        },
      ],
    }

    render(<SupplementFilesList progress={progress} providerShortLabel="ABC" />)

    const rows = within(screen.getByRole('list', { name: 'Supplement files' })).getAllByRole('listitem')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('Mystery_S7.xlsx')
    expect(rows[0]).toHaveTextContent('This screen cannot describe this file yet.')
    expect(rows[1]).toHaveTextContent('Unnamed file')
    expect(rows[1]).toHaveTextContent('This screen cannot describe this file yet.')
    expect(screen.queryByText(/future_unrecognized_reason/)).not.toBeInTheDocument()
  })

  it('uses configured non-ABC provider labels and falls back generically when omitted', () => {
    const progress = parseSupplementJobProgress(supplementWaitMetadataFixture.mixed_waiting, {
      currentStage: WAITING_FOR_SUPPLEMENTS_STAGE,
    })

    const { rerender } = render(
      <SupplementFilesList progress={progress} providerShortLabel="Genome Archive" />,
    )
    expect(screen.getByText('Waiting for Genome Archive')).toBeInTheDocument()
    expect(screen.getByText(/Genome Archive has until .* to convert them\./)).toBeInTheDocument()
    expect(screen.queryByText(/ABC/)).not.toBeInTheDocument()

    rerender(<SupplementFilesList progress={progress} />)
    expect(screen.getByText('Waiting for the literature source')).toBeInTheDocument()
    expect(screen.getByText(/The literature source has until .* to convert them\./)).toBeInTheDocument()
    expect(screen.queryByText(/ABC/)).not.toBeInTheDocument()
  })
})
