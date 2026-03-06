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
});
