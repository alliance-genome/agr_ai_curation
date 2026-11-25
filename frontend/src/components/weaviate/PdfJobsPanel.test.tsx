import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '../../test/test-utils';
import supplementAddMetadataFixture from '@/test/fixtures/supplements/supplement_add_metadata.json';
import supplementWaitMetadataFixture from '@/test/fixtures/supplements/supplement_wait_metadata.json';
import {
  ADDING_SUPPLEMENTS_STAGE,
  WAITING_FOR_SUPPLEMENTS_STAGE,
} from '@/features/documents/supplementProgress';
import PdfJobsPanel from './PdfJobsPanel';
import { formatSupplementWaitEndTime } from './paperFileReasonText';
import type { PdfProcessingJob } from '../../services/weaviate';
import {
  DOCUMENT_LOADING_STORAGE_KEY,
  DOCUMENT_LOAD_START_EVENT,
} from '../../features/documents/documentLoadEvents';

const mockNavigate = vi.fn();
const openCurationWorkspaceMock = vi.fn();

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

vi.mock('@/features/curation/navigation/openCurationWorkspace', async () => {
  const actual = await vi.importActual<typeof import('@/features/curation/navigation/openCurationWorkspace')>(
    '@/features/curation/navigation/openCurationWorkspace'
  );

  return {
    ...actual,
    openCurationWorkspace: (options: unknown) => openCurationWorkspaceMock(options),
  };
});

const buildJobs = (count: number): PdfProcessingJob[] => {
  const now = new Date('2026-03-04T00:00:00.000Z').toISOString();
  return Array.from({ length: count }, (_value, index) => {
    const jobIndex = index + 1;
    return {
      job_id: `job-${jobIndex}`,
      document_id: `doc-${jobIndex}`,
      user_id: 123,
      filename: `file-${jobIndex}.pdf`,
      status: 'running',
      current_stage: 'extracting',
      progress_percentage: Math.min(jobIndex * 10, 100),
      message: 'Processing...',
      process_id: `proc-${jobIndex}`,
      cancel_requested: false,
      error_message: null,
      metadata: null,
      created_at: now,
      started_at: now,
      updated_at: now,
      completed_at: null,
    };
  });
};

describe('PdfJobsPanel', () => {
  beforeEach(() => {
    mockNavigate.mockReset();
    openCurationWorkspaceMock.mockReset();
    sessionStorage.clear();
  });

  it('starts collapsed when there are no active jobs', () => {
    render(<PdfJobsPanel jobs={[]} />);

    expect(screen.getByText('Panel collapsed')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Expand PDF jobs/i })).toBeInTheDocument();
    expect(screen.queryByText('No PDF jobs in the last 7 days.')).not.toBeInTheDocument();
  });

  it('expands and shows empty state when toggled', () => {
    render(<PdfJobsPanel jobs={[]} />);

    fireEvent.click(screen.getByRole('button', { name: /Expand PDF jobs/i }));

    expect(screen.getByRole('button', { name: /Collapse PDF jobs/i })).toBeInTheDocument();
    expect(screen.getByText('No PDF jobs in the last 7 days.')).toBeInTheDocument();
  });

  it('shows 5 rows by default', () => {
    render(<PdfJobsPanel jobs={buildJobs(7)} />);

    expect(screen.getByText('file-1.pdf')).toBeInTheDocument();
    expect(screen.getByText('file-5.pdf')).toBeInTheDocument();
    expect(screen.queryByText('file-6.pdf')).not.toBeInTheDocument();
    expect(screen.getByText('Showing 1-5 of 7')).toBeInTheDocument();
  });

  it('paginates to the next page', () => {
    render(<PdfJobsPanel jobs={buildJobs(7)} />);

    fireEvent.click(screen.getByRole('button', { name: /Go to page 2/i }));

    expect(screen.getByText('file-6.pdf')).toBeInTheDocument();
    expect(screen.getByText('file-7.pdf')).toBeInTheDocument();
    expect(screen.queryByText('file-1.pdf')).not.toBeInTheDocument();
    expect(screen.getByText('Showing 6-7 of 7')).toBeInTheDocument();
  });

  it('shows red cancel button and calls handler when cancellable', () => {
    const onCancelJob = vi.fn().mockResolvedValue(undefined);
    const job = {
      ...buildJobs(1)[0],
      status: 'running' as const,
    };

    render(<PdfJobsPanel jobs={[job]} onCancelJob={onCancelJob} />);

    const cancelButton = screen.getByRole('button', { name: 'Cancel' });
    expect(cancelButton).toBeEnabled();
    fireEvent.click(cancelButton);
    expect(onCancelJob).toHaveBeenCalledWith(job.job_id);
  });

  it('shows provider conversion progress details from job metadata', () => {
    const job = {
      ...buildJobs(1)[0],
      message: 'ABC Literature conversion running',
      metadata: {
        document_source: {
          conversion_status: 'running',
          per_file_progress: [
            {
              source: { display_name: 'paper', file_class: 'main' },
              converted: { display_name: 'paper_merged', file_class: 'converted_merged_main' },
              status: 'pending',
              error: null,
            },
          ],
        },
      },
    };

    render(<PdfJobsPanel jobs={[job]} />);

    expect(screen.getByText('10% • ABC Literature conversion running')).toBeInTheDocument();
    expect(screen.getByText('paper pending')).toBeInTheDocument();
  });

  it('shows disabled gray cancel button when cancellation is unavailable', () => {
    const onCancelJob = vi.fn().mockResolvedValue(undefined);
    const job = {
      ...buildJobs(1)[0],
      status: 'completed' as const,
    };

    render(<PdfJobsPanel jobs={[job]} onCancelJob={onCancelJob} />);
    fireEvent.click(screen.getByRole('button', { name: /Expand PDF jobs/i }));

    const cancelButton = screen.getByRole('button', { name: 'Cancel' });
    expect(cancelButton).toBeDisabled();
  });

  it('can hide and restore a terminal job from the list', () => {
    const completedJob = {
      ...buildJobs(1)[0],
      status: 'completed' as const,
      filename: 'completed-file.pdf',
    };

    render(<PdfJobsPanel jobs={[completedJob]} />);
    fireEvent.click(screen.getByRole('button', { name: /Expand PDF jobs/i }));

    expect(screen.getByText('completed-file.pdf')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Hide from this list/i }));
    expect(screen.queryByText('completed-file.pdf')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Show 1 hidden PDF jobs/i }));
    expect(screen.getByText('completed-file.pdf')).toBeInTheDocument();
  });

  it('offers Load for chat and Load for curation only on completed jobs', () => {
    const [runningJob, completedJob, failedJob, cancelledJob, pendingJob] = buildJobs(5);
    const jobs: PdfProcessingJob[] = [
      { ...runningJob, status: 'running' },
      { ...completedJob, status: 'completed', progress_percentage: 100 },
      { ...failedJob, status: 'failed' },
      { ...cancelledJob, status: 'cancelled' },
      { ...pendingJob, status: 'pending' },
    ];

    render(<PdfJobsPanel jobs={jobs} />);

    expect(screen.getAllByRole('button', { name: 'Load for chat' })).toHaveLength(1);
    expect(screen.getAllByRole('button', { name: 'Load for curation' })).toHaveLength(1);
  });

  it('loads a completed job document into chat the same way as the document list', () => {
    const loadStartListener = vi.fn();
    window.addEventListener(DOCUMENT_LOAD_START_EVENT, loadStartListener);
    const job: PdfProcessingJob = {
      ...buildJobs(1)[0],
      status: 'completed',
      document_id: 'doc-ready',
      filename: 'ready-paper.pdf',
    };

    render(<PdfJobsPanel jobs={[job]} />);
    fireEvent.click(screen.getByRole('button', { name: /Expand PDF jobs/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Load for chat' }));

    expect(sessionStorage.getItem(DOCUMENT_LOADING_STORAGE_KEY)).toBe('true');
    expect(loadStartListener).toHaveBeenCalledTimes(1);
    expect(mockNavigate).toHaveBeenCalledWith('/', {
      state: {
        loadForChatDocument: {
          id: 'doc-ready',
          filename: 'ready-paper.pdf',
        },
      },
    });

    window.removeEventListener(DOCUMENT_LOAD_START_EVENT, loadStartListener);
  });

  it('opens curation for a completed job document', async () => {
    openCurationWorkspaceMock.mockResolvedValue('session-1');
    const job: PdfProcessingJob = {
      ...buildJobs(1)[0],
      status: 'completed',
      document_id: 'doc-ready',
    };

    render(<PdfJobsPanel jobs={[job]} />);
    fireEvent.click(screen.getByRole('button', { name: /Expand PDF jobs/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Load for curation' }));

    await waitFor(() => {
      expect(openCurationWorkspaceMock).toHaveBeenCalledWith(
        expect.objectContaining({ documentId: 'doc-ready' })
      );
    });
  });

  it('renders Card 39 message, N10 wait-end time, supplement rows, and working Cancel during waiting_for_supplements', () => {
    const onCancelJob = vi.fn().mockResolvedValue(undefined);
    const job: PdfProcessingJob = {
      ...buildJobs(1)[0],
      job_id: 'job-wait-1',
      status: 'running',
      current_stage: WAITING_FOR_SUPPLEMENTS_STAGE,
      progress_percentage: 0,
      message: 'Main paper ready. Waiting for ABC to convert 1 supplement; you can use the paper and leave this page.',
      metadata: supplementWaitMetadataFixture.mixed_waiting,
    };

    render(
      <PdfJobsPanel
        jobs={[job]}
        onCancelJob={onCancelJob}
        providerShortLabel="ABC"
        providerLabel="ABC Literature"
      />,
    );

    expect(
      screen.getByText(
        '0% • Main paper ready. Waiting for ABC to convert 1 supplement; you can use the paper and leave this page.',
      ),
    ).toBeInTheDocument();

    const expectedTime = formatSupplementWaitEndTime(
      supplementWaitMetadataFixture.mixed_waiting.supplement_wait_ends_at,
    );
    expect(expectedTime).not.toBeNull();
    expect(screen.getByText(`ABC has until ${expectedTime} to convert them.`)).toBeInTheDocument();

    const rows = within(screen.getByRole('list', { name: 'Supplement files' })).getAllByRole('listitem');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent('Converted_S1.xlsx');
    expect(rows[0]).toHaveTextContent('Waiting to be added');
    expect(rows[0]).not.toHaveTextContent('Waiting for the main paper');
    expect(rows[1]).toHaveTextContent('Pending_S2.docx');
    expect(rows[1]).toHaveTextContent('Waiting for ABC');

    const cancelButton = screen.getByRole('button', { name: 'Cancel' });
    expect(cancelButton).toBeEnabled();
    fireEvent.click(cancelButton);
    expect(onCancelJob).toHaveBeenCalledWith('job-wait-1');
  });

  it('uses configured non-ABC provider labels and generic fallbacks without hardcoding ABC', () => {
    const waitingJob: PdfProcessingJob = {
      ...buildJobs(1)[0],
      job_id: 'job-wait-non-abc',
      status: 'running',
      current_stage: WAITING_FOR_SUPPLEMENTS_STAGE,
      progress_percentage: 0,
      message: 'Waiting for provider conversion',
      metadata: supplementWaitMetadataFixture.mixed_waiting,
    };
    const conversionStatusJob: PdfProcessingJob = {
      ...buildJobs(1)[0],
      job_id: 'job-conv-status',
      status: 'running',
      current_stage: 'converting',
      progress_percentage: 20,
      message: 'Provider conversion running',
      metadata: {
        document_source: {
          conversion_status: 'running',
        },
      },
    };
    const expectedTime = formatSupplementWaitEndTime(
      supplementWaitMetadataFixture.mixed_waiting.supplement_wait_ends_at,
    );

    const { rerender } = render(
      <PdfJobsPanel
        jobs={[waitingJob, conversionStatusJob]}
        providerShortLabel="Example"
        providerLabel="Example Archive"
      />,
    );

    expect(screen.getByText(`Example has until ${expectedTime} to convert them.`)).toBeInTheDocument();
    expect(screen.getByText('Waiting for Example')).toBeInTheDocument();
    expect(screen.getByText('Example conversion status: running')).toBeInTheDocument();
    expect(screen.queryByText(/ABC/)).not.toBeInTheDocument();

    rerender(
      <PdfJobsPanel
        jobs={[waitingJob, conversionStatusJob]}
        providerLabel="Example Archive"
      />,
    );

    expect(screen.getByText(`Example Archive has until ${expectedTime} to convert them.`)).toBeInTheDocument();
    expect(screen.getByText('Waiting for Example Archive')).toBeInTheDocument();
    expect(screen.getByText('Example Archive conversion status: running')).toBeInTheDocument();
    expect(screen.queryByText(/ABC/)).not.toBeInTheDocument();

    rerender(<PdfJobsPanel jobs={[waitingJob, conversionStatusJob]} />);

    expect(screen.getByText(`The literature source has until ${expectedTime} to convert them.`)).toBeInTheDocument();
    expect(screen.getByText('Waiting for the literature source')).toBeInTheDocument();
    expect(screen.getByText('Conversion status: running')).toBeInTheDocument();
    expect(screen.queryByText(/ABC/)).not.toBeInTheDocument();
  });

  it('distinguishes pre-main queued entries (N7) from post-main queued entries and keeps Cancel active', () => {
    const onCancelJob = vi.fn().mockResolvedValue(undefined);
    const preMainJob: PdfProcessingJob = {
      ...buildJobs(1)[0],
      job_id: 'job-pre-main',
      filename: 'pre-main.pdf',
      status: 'running',
      current_stage: 'extracting',
      progress_percentage: 35,
      message: 'Extracting main paper',
      metadata: {
        supplements: [
          {
            provider_file_id: '30',
            display_name: 'PreMain_S1.xlsx',
            state: 'queued',
            reason_code: null,
            observed_at: null,
            file_id: null,
            measured_value: null,
            limit_value: null,
          },
        ],
      },
    };
    const addingStageJob: PdfProcessingJob = {
      ...buildJobs(1)[0],
      job_id: 'job-adding',
      filename: 'post-main.pdf',
      status: 'running',
      current_stage: ADDING_SUPPLEMENTS_STAGE,
      progress_percentage: 0,
      message: 'Adding supplements',
      metadata: {
        job_kind: 'supplement_wait',
        supplement_wait_ends_at: '2026-11-24T15:30:00+00:00',
        supplements: [
          {
            provider_file_id: '31',
            display_name: 'PostMainQueued_S1.xlsx',
            state: 'queued',
            reason_code: null,
            observed_at: null,
            file_id: null,
            measured_value: null,
            limit_value: null,
          },
          {
            provider_file_id: '32',
            display_name: 'PostMainAdding_S2.csv',
            state: 'adding',
            reason_code: null,
            observed_at: null,
            file_id: null,
            measured_value: null,
            limit_value: null,
          },
        ],
      },
    };

    render(
      <PdfJobsPanel
        jobs={[preMainJob, addingStageJob]}
        onCancelJob={onCancelJob}
        providerShortLabel="ABC"
      />,
    );

    const lists = screen.getAllByRole('list', { name: 'Supplement files' });
    expect(lists).toHaveLength(2);

    const preMainRow = within(lists[0]).getByRole('listitem');
    expect(preMainRow).toHaveTextContent('PreMain_S1.xlsx');
    expect(preMainRow).toHaveTextContent('Waiting for the main paper');
    expect(preMainRow).not.toHaveTextContent('Waiting to be added');

    const postMainRows = within(lists[1]).getAllByRole('listitem');
    expect(postMainRows[0]).toHaveTextContent('PostMainQueued_S1.xlsx');
    expect(postMainRows[0]).toHaveTextContent('Waiting to be added');
    expect(postMainRows[0]).not.toHaveTextContent('Waiting for the main paper');
    expect(postMainRows[1]).toHaveTextContent('PostMainAdding_S2.csv');
    expect(postMainRows[1]).toHaveTextContent('Being added');

    // No wait-end deadline line outside waiting_for_supplements.
    expect(screen.queryByText(/has until/)).not.toBeInTheDocument();

    const cancelButtons = screen.getAllByRole('button', { name: 'Cancel' });
    expect(cancelButtons).toHaveLength(2);
    expect(cancelButtons[0]).toBeEnabled();
    expect(cancelButtons[1]).toBeEnabled();
    fireEvent.click(cancelButtons[0]);
    fireEvent.click(cancelButtons[1]);
    expect(onCancelJob).toHaveBeenNthCalledWith(1, 'job-pre-main');
    expect(onCancelJob).toHaveBeenNthCalledWith(2, 'job-adding');
  });

  it('renders supplement-add outcomes from the real backend fixture plus unknown codes as N29 and legacy N17', () => {
    const supplementAddJob: PdfProcessingJob = {
      ...buildJobs(1)[0],
      job_id: 'job-add-1',
      filename: 'paper-with-adds.pdf',
      status: 'completed',
      current_stage: 'completed',
      progress_percentage: 100,
      message: 'Added 1 of 11 files.',
      metadata: {
        ...supplementAddMetadataFixture,
        supplements: [
          ...supplementAddMetadataFixture.supplements,
          {
            provider_file_id: '110',
            display_name: 'FutureReason_S10.xlsx',
            state: 'left_out',
            reason_code: 'brand_new_future_code',
            observed_at: null,
            file_id: null,
            measured_value: null,
            limit_value: null,
          },
        ],
      },
    };

    render(<PdfJobsPanel jobs={[supplementAddJob]} providerShortLabel="ABC" />);
    fireEvent.click(screen.getByRole('button', { name: /Expand PDF jobs/i }));

    expect(screen.getByText('100% • Added 1 of 11 files.')).toBeInTheDocument();

    const rows = within(screen.getByRole('list', { name: 'Supplement files' })).getAllByRole('listitem');
    expect(rows).toHaveLength(11);
    expect(rows[0]).toHaveTextContent('Table_S1.xlsx');
    expect(rows[0]).toHaveTextContent('Added');
    expect(rows[1]).toHaveTextContent('Unnamed file');
    expect(rows[1]).toHaveTextContent('Not added: ABC no longer lists this file for the paper.');
    expect(rows[2]).toHaveTextContent('Legacy_S2.pdf');
    expect(rows[2]).toHaveTextContent('Not added: PDF supplements could not be added when this import ran.');
    expect(rows[3]).toHaveTextContent('This PDF is larger than 20 MB, so it was not added.');
    expect(rows[4]).toHaveTextContent('This PDF could not be opened, so it was not added.');
    expect(rows[5]).toHaveTextContent('You can read this file\'s text in ABC but not its PDF, so it was not added.');
    expect(rows[6]).toHaveTextContent('Not added: the paper already has the most files allowed (20).');
    expect(rows[7]).toHaveTextContent('Not added: the paper would hold more text than AI Curation reads (2 MB).');
    expect(rows[8]).toHaveTextContent('Already in this paper');
    expect(rows[9]).toHaveTextContent('A table in its converted text has rows that do not match its header');
    expect(rows[10]).toHaveTextContent('FutureReason_S10.xlsx');
    expect(rows[10]).toHaveTextContent('This screen cannot describe this file yet.');
    expect(screen.queryByText(/brand_new_future_code/)).not.toBeInTheDocument();
  });

  it('renders N13 per file on cancelled supplement jobs and omits N10 wait-end text on terminal or cancel_requested jobs', () => {
    const cancelledEntry = supplementWaitMetadataFixture.all_states_and_outcomes.supplements.find(
      (entry) => entry.reason_code === 'import_cancelled',
    )!;
    const cancelledJob: PdfProcessingJob = {
      ...buildJobs(1)[0],
      job_id: 'job-cancelled-supp',
      filename: 'cancelled-paper.pdf',
      status: 'cancelled',
      current_stage: WAITING_FOR_SUPPLEMENTS_STAGE,
      progress_percentage: 0,
      message: 'Cancelled',
      metadata: {
        job_kind: 'supplement_wait',
        supplement_wait_ends_at: supplementWaitMetadataFixture.mixed_waiting.supplement_wait_ends_at,
        supplements: [
          cancelledEntry,
          {
            ...cancelledEntry,
            provider_file_id: '5b',
            display_name: 'Cancelled_S5b.docx',
          },
        ],
      },
    };
    const completedJob: PdfProcessingJob = {
      ...buildJobs(1)[0],
      job_id: 'job-finished-supp',
      filename: 'finished-paper.pdf',
      status: 'completed',
      current_stage: 'completed',
      progress_percentage: 100,
      message: 'Supplements finished',
      metadata: supplementWaitMetadataFixture.finished_with_wait_timestamp,
    };
    const cancelRequestedJob: PdfProcessingJob = {
      ...buildJobs(1)[0],
      job_id: 'job-cancel-req-supp',
      filename: 'cancelling-paper.pdf',
      status: 'cancel_requested',
      current_stage: WAITING_FOR_SUPPLEMENTS_STAGE,
      progress_percentage: 0,
      cancel_requested: true,
      message: 'Cancel requested',
      metadata: supplementWaitMetadataFixture.mixed_waiting,
    };

    render(
      <PdfJobsPanel
        jobs={[cancelledJob, completedJob, cancelRequestedJob]}
        providerShortLabel="ABC"
      />,
    );

    expect(screen.queryByText(/has until/)).not.toBeInTheDocument();

    const lists = screen.getAllByRole('list', { name: 'Supplement files' });
    const cancelledRows = within(lists[0]).getAllByRole('listitem');
    expect(cancelledRows).toHaveLength(2);
    expect(cancelledRows[0]).toHaveTextContent('Cancelled_S5.xlsx');
    expect(cancelledRows[0]).toHaveTextContent('Not added: the import was cancelled.');
    expect(cancelledRows[1]).toHaveTextContent('Cancelled_S5b.docx');
    expect(cancelledRows[1]).toHaveTextContent('Not added: the import was cancelled.');

    const completedRows = within(lists[1]).getAllByRole('listitem');
    expect(completedRows).toHaveLength(2);
    expect(completedRows[0]).toHaveTextContent('Done_S1.xlsx');
    expect(completedRows[0]).toHaveTextContent('Added');
    expect(completedRows[1]).toHaveTextContent('Failed_S2.xlsx');
    expect(completedRows[1]).toHaveTextContent('Not added: adding this paper\'s supplements failed.');
  });

  it('renders no supplement list and never "0 files" for ordinary uploads or main-only imports without supplements', () => {
    const ordinaryUploadJob: PdfProcessingJob = {
      ...buildJobs(1)[0],
      job_id: 'job-upload-1',
      filename: 'upload-only.pdf',
      status: 'running',
      current_stage: 'extracting',
      progress_percentage: 45,
      message: 'Extracting text',
      metadata: null,
    };
    const mainOnlyImportJob: PdfProcessingJob = {
      ...buildJobs(1)[0],
      job_id: 'job-main-only',
      filename: 'main-only.pdf',
      status: 'completed',
      current_stage: 'completed',
      progress_percentage: 100,
      message: 'Processing completed',
      metadata: {
        supplements: [],
        document_source: {
          converted_classes: ['main'],
        },
      },
    };
    const queuedSlotWaitJob: PdfProcessingJob = {
      ...buildJobs(1)[0],
      job_id: 'job-slot-wait',
      filename: 'slot-wait.pdf',
      status: 'running',
      current_stage: WAITING_FOR_SUPPLEMENTS_STAGE,
      progress_percentage: 0,
      message: 'Waiting for ABC',
      metadata: {
        job_kind: 'supplement_wait',
      },
    };

    render(
      <PdfJobsPanel
        jobs={[ordinaryUploadJob, mainOnlyImportJob, queuedSlotWaitJob]}
        providerShortLabel="ABC"
      />,
    );

    expect(screen.getByText('upload-only.pdf')).toBeInTheDocument();
    expect(screen.getByText('main-only.pdf')).toBeInTheDocument();
    expect(screen.getByText('slot-wait.pdf')).toBeInTheDocument();
    expect(screen.getByText('Converted text available: main')).toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Supplement files' })).not.toBeInTheDocument();
    expect(screen.queryByText(/0 files/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/has until/)).not.toBeInTheDocument();
  });
});
