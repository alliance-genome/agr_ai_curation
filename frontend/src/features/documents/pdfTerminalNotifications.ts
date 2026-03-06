export type PdfTerminalStatus = 'completed' | 'failed' | 'cancelled';

type PdfTerminalNotificationSeverity = 'success' | 'error' | 'info';

export interface PdfTerminalNotificationJob {
  job_id: string;
  status: string;
  filename?: string | null;
  document_id?: string | null;
  cancel_requested?: boolean | null;
}

export interface PdfTerminalNotificationDetail {
  key: string;
  status: PdfTerminalStatus;
  message: string;
  severity: PdfTerminalNotificationSeverity;
}

const normalizePdfTerminalStatus = (status: string): string => {
  const normalized = String(status).trim().toLowerCase();
  if (normalized === 'canceled') {
    return 'cancelled';
  }
  return normalized;
};

export const classifyPdfTerminalStatus = (
  job: Pick<PdfTerminalNotificationJob, 'status' | 'cancel_requested'>
): PdfTerminalStatus | null => {
  const status = normalizePdfTerminalStatus(job.status);
  if (!['completed', 'failed', 'cancelled'].includes(status)) {
    return null;
  }

  if (status === 'failed' && Boolean(job.cancel_requested)) {
    // Ignore transient failed snapshots while cancellation finalization is in progress.
    return null;
  }

  return status as PdfTerminalStatus;
};

export const buildPdfTerminalNotification = (
  job: PdfTerminalNotificationJob
): PdfTerminalNotificationDetail | null => {
  const status = classifyPdfTerminalStatus(job);
  if (!status) {
    return null;
  }

  const filename = job.filename ?? job.document_id ?? job.job_id;
  if (status === 'completed') {
    return {
      key: `${job.job_id}:${status}`,
      status,
      message: `PDF processing completed: ${filename}`,
      severity: 'success',
    };
  }
  if (status === 'cancelled') {
    return {
      key: `${job.job_id}:${status}`,
      status,
      message: `PDF processing cancelled: ${filename}`,
      severity: 'info',
    };
  }
  return {
    key: `${job.job_id}:${status}`,
    status,
    message: `PDF processing failed: ${filename}`,
    severity: 'error',
  };
};
