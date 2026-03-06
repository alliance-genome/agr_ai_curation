import { describe, expect, it } from 'vitest';
import { buildPdfTerminalNotification, classifyPdfTerminalStatus } from './pdfTerminalNotifications';

describe('pdfTerminalNotifications', () => {
  it('classifies completed jobs as terminal', () => {
    expect(classifyPdfTerminalStatus({ status: 'completed', cancel_requested: false })).toBe('completed');
  });

  it('ignores transient failed snapshots when cancellation is requested', () => {
    expect(classifyPdfTerminalStatus({ status: 'failed', cancel_requested: true })).toBeNull();
  });

  it('normalizes canceled to cancelled', () => {
    expect(classifyPdfTerminalStatus({ status: 'canceled', cancel_requested: false })).toBe('cancelled');
  });

  it('builds a cancelled notification payload', () => {
    expect(
      buildPdfTerminalNotification({
        job_id: 'job-1',
        status: 'cancelled',
        filename: 'paper.pdf',
        document_id: 'doc-1',
        cancel_requested: true,
      })
    ).toEqual({
      key: 'job-1:cancelled',
      status: 'cancelled',
      message: 'PDF processing cancelled: paper.pdf',
      severity: 'info',
    });
  });

  it('builds a failed notification payload when failure is terminal', () => {
    expect(
      buildPdfTerminalNotification({
        job_id: 'job-2',
        status: 'failed',
        filename: 'broken.pdf',
        document_id: 'doc-2',
        cancel_requested: false,
      })
    ).toEqual({
      key: 'job-2:failed',
      status: 'failed',
      message: 'PDF processing failed: broken.pdf',
      severity: 'error',
    });
  });

  it('falls back to document_id and then job_id when filename is missing', () => {
    expect(
      buildPdfTerminalNotification({
        job_id: 'job-3',
        status: 'completed',
        filename: undefined,
        document_id: 'doc-3',
        cancel_requested: false,
      })
    ).toEqual({
      key: 'job-3:completed',
      status: 'completed',
      message: 'PDF processing completed: doc-3',
      severity: 'success',
    });

    expect(
      buildPdfTerminalNotification({
        job_id: 'job-4',
        status: 'completed',
        filename: undefined,
        document_id: undefined,
        cancel_requested: false,
      })
    ).toEqual({
      key: 'job-4:completed',
      status: 'completed',
      message: 'PDF processing completed: job-4',
      severity: 'success',
    });
  });
});
