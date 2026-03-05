import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '../../test/test-utils';
import PdfJobsPanel from './PdfJobsPanel';
import type { PdfProcessingJob } from '../../services/weaviate';

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
});
