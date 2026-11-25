import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { parseSupplementJobProgress, type SupplementJobProgress } from '@/features/documents/supplementProgress'

import SupplementFilesList from './SupplementFilesList'
import { formatSupplementWaitEndTime } from './paperFileReasonText'

describe('SupplementFilesList', () => {
  it('renders nothing when progress or metadata has no supplement entries', () => {
    for (const props of [
      {},
      { progress: null },
      { progress: { supplements: [], supplement_wait_ends_at: '2026-11-24T15:30:00+00:00', job_kind: 'supplement_wait' as const } },
      { metadata: null },
      { metadata: {} },
      { metadata: { supplements: [] } },
      { metadata: { supplements: [{ bad: true }] } },
    ]) {
      const { container } = render(<SupplementFilesList {...props} heading="Adding files" />)
      expect(container).toBeEmptyDOMElement()
    }
  })

  it('renders every supplement state and shows N10 when a file is waiting with a deadline', () => {
    const waitEndsAt = '2026-11-24T15:30:00+00:00'
    const progress: SupplementJobProgress = {
      job_kind: 'supplement_wait',
      supplement_wait_ends_at: waitEndsAt,
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
        {
          provider_file_id: '2',
          display_name: 'Waiting_S2.docx',
          state: 'waiting',
          reason_code: null,
          observed_at: null,
          file_id: null,
          measured_value: null,
          limit_value: null,
        },
        {
          provider_file_id: '3',
          display_name: 'Adding_S3.csv',
          state: 'adding',
          reason_code: null,
          observed_at: null,
          file_id: null,
          measured_value: null,
          limit_value: null,
        },
        {
          provider_file_id: '4',
          display_name: 'Added_S4.pdf',
          state: 'added',
          reason_code: null,
          observed_at: null,
          file_id: 'file-uuid-4',
          measured_value: null,
          limit_value: null,
        },
        {
          provider_file_id: '5',
          display_name: 'Cancelled_S5.xlsx',
          state: 'left_out',
          reason_code: 'import_cancelled',
          observed_at: '2026-11-24T15:05:00+00:00',
          file_id: null,
          measured_value: null,
          limit_value: null,
        },
      ],
    }

    render(<SupplementFilesList progress={progress} heading="Adding files" />)

    expect(screen.getByText('Adding files')).toBeInTheDocument()
    const expectedTime = formatSupplementWaitEndTime(waitEndsAt)
    expect(screen.getByText(`ABC has until ${expectedTime} to convert them.`)).toBeInTheDocument()

    const list = screen.getByRole('list', { name: 'Supplement files' })
    const rows = within(list).getAllByRole('listitem')
    expect(rows).toHaveLength(5)
    expect(rows[0]).toHaveTextContent('Queued_S1.xlsx')
    expect(rows[0]).toHaveTextContent('Waiting for the main paper')
    expect(rows[1]).toHaveTextContent('Waiting_S2.docx')
    expect(rows[1]).toHaveTextContent('Waiting for ABC')
    expect(rows[2]).toHaveTextContent('Adding_S3.csv')
    expect(rows[2]).toHaveTextContent('Being added')
    expect(rows[3]).toHaveTextContent('Added_S4.pdf')
    expect(rows[3]).toHaveTextContent('Added')
    expect(rows[4]).toHaveTextContent('Cancelled_S5.xlsx')
    expect(rows[4]).toHaveTextContent('Not added')
    expect(rows[4]).toHaveTextContent('Not added: the import was cancelled.')
  })

  it('omits N10 wait-end line once no supplement is waiting', () => {
    const progress = parseSupplementJobProgress({
      job_kind: 'supplement_wait',
      supplement_wait_ends_at: '2026-11-24T15:30:00+00:00',
      supplements: [
        {
          provider_file_id: '10',
          display_name: 'Done_S1.xlsx',
          state: 'added',
          reason_code: null,
          observed_at: null,
          file_id: 'file-10',
          measured_value: null,
          limit_value: null,
        },
        {
          provider_file_id: '11',
          display_name: 'Failed_S2.xlsx',
          state: 'left_out',
          reason_code: 'supplement_stage_failed',
          observed_at: '2026-11-24T15:05:00+00:00',
          file_id: null,
          measured_value: null,
          limit_value: null,
        },
      ],
    })

    render(<SupplementFilesList progress={progress} compact />)

    expect(screen.queryByText(/ABC has until/)).not.toBeInTheDocument()
    expect(screen.getByText('Done_S1.xlsx')).toBeInTheDocument()
    expect(screen.getByText('Added')).toBeInTheDocument()
    expect(screen.getByText('Failed_S2.xlsx')).toBeInTheDocument()
    expect(screen.getByText('Not added: adding this paper\'s supplements failed.')).toBeInTheDocument()
  })

  it('renders approved reasons from raw metadata including historical N17, limits, Card 40, Unnamed file, and N29 fallback', () => {
    render(
      <SupplementFilesList
        metadata={{
          job_kind: 'supplement_add',
          supplements: [
            {
              provider_file_id: '20',
              display_name: 'Old_S1.pdf',
              state: 'left_out',
              reason_code: 'pdf_supplement_not_supported_yet',
              observed_at: null,
              file_id: null,
              measured_value: null,
              limit_value: null,
            },
            {
              provider_file_id: '21',
              display_name: 'Huge_S2.pdf',
              state: 'left_out',
              reason_code: 'pdf_too_large',
              observed_at: null,
              file_id: null,
              measured_value: 25_000_000,
              limit_value: 20_000_000,
            },
            {
              provider_file_id: '22',
              display_name: 'Overflow_S3.xlsx',
              state: 'left_out',
              reason_code: 'paper_file_limit',
              observed_at: null,
              file_id: null,
              measured_value: 21,
              limit_value: 20,
            },
            {
              provider_file_id: '23',
              display_name: 'Late_S4.docx',
              state: 'left_out',
              reason_code: 'not_converted_in_time',
              observed_at: '2026-11-24T15:30:00+00:00',
              file_id: null,
              measured_value: null,
              limit_value: null,
            },
            {
              provider_file_id: '24',
              display_name: null,
              state: 'left_out',
              reason_code: 'future_unrecognized_reason',
              observed_at: null,
              file_id: null,
              measured_value: null,
              limit_value: null,
            },
          ],
        }}
      />,
    )

    const rows = within(screen.getByRole('list', { name: 'Supplement files' })).getAllByRole('listitem')
    expect(rows).toHaveLength(5)
    expect(rows[0]).toHaveTextContent('Not added: PDF supplements could not be added when this import ran.')
    expect(rows[1]).toHaveTextContent('This PDF is larger than 20 MB, so it was not added.')
    expect(rows[2]).toHaveTextContent('Not added: the paper already has the most files allowed (20).')
    expect(rows[3]).toHaveTextContent('ABC had not converted this file when the import stopped waiting.')
    expect(rows[4]).toHaveTextContent('Unnamed file')
    expect(rows[4]).toHaveTextContent('This screen cannot describe this file yet.')
    expect(screen.queryByText(/future_unrecognized_reason/)).not.toBeInTheDocument()
  })
})
