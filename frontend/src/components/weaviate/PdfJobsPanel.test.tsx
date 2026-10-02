import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '../../test/test-utils';
import PdfJobsPanel from './PdfJobsPanel';
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
});
