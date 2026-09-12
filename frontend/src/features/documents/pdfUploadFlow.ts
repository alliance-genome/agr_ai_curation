import {
  PDF_UPLOAD_MAX_SELECTED_FILES,
  PDF_PROGRESS_CONNECT_TIMEOUT_MS,
  PDF_PROGRESS_TIMEOUT_MS,
  PDF_PROGRESS_POLL_INTERVAL_MS,
} from './documentIntakeConfig';

const TERMINAL_STAGES = new Set([
  'completed',
  'failed',
  'error',
  'cancelled',
  'canceled',
  'timeout',
]);

const STAGE_PROGRESS_FALLBACK: Record<string, number> = {
  pending: 5,
  upload: 10,
  uploading: 10,
  parsing: 35,
  chunking: 55,
  embedding: 75,
  storing: 90,
  completed: 100,
  failed: 100,
  cancelled: 100,
  canceled: 100,
  error: 100,
  timeout: 100,
};

export class PdfProgressStreamError extends Error {
  constructor(public readonly kind: 'transport' | 'producer' | 'malformed', message: string) {
    super(message);
    this.name = 'PdfProgressStreamError';
  }
}

const parseProgressEvent = (data: string): ProgressSsePayload => {
  let payload: unknown;
  try {
    payload = JSON.parse(data);
  } catch {
    throw new PdfProgressStreamError('malformed', 'Malformed upload progress event.');
  }
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    throw new PdfProgressStreamError('malformed', 'Malformed upload progress event.');
  }
  const parsed = payload as ProgressSsePayload;
  if ('error' in parsed && typeof parsed.error === 'string' && parsed.error.trim()) {
    throw new PdfProgressStreamError('producer', parsed.error);
  }
  if ('error' in parsed || typeof parsed.stage !== 'string' || !parsed.stage.trim()
    || (parsed.progress !== undefined && (typeof parsed.progress !== 'number' || !Number.isFinite(parsed.progress)))
    || (parsed.message !== undefined && typeof parsed.message !== 'string')
    || (parsed.final !== undefined && typeof parsed.final !== 'boolean')) {
    throw new PdfProgressStreamError('malformed', 'Malformed upload progress event.');
  }
  return parsed;
};

interface UploadErrorDetail {
  existing_document_id?: string;
  existing_filename?: string;
  uploaded_at?: string;
  suggestion?: string;
  message?: string;
}

interface UploadErrorPayload {
  detail?: UploadErrorDetail | string;
}

interface UploadResponsePayload {
  document_id?: string;
  job_id?: string;
}

interface DocumentStatusPipelinePayload {
  current_stage?: string | null;
  progress_percentage?: number | null;
  message?: string | null;
}

interface DocumentStatusPayload {
  processing_status?: string | null;
  pipeline_status?: DocumentStatusPipelinePayload | null;
  job_status?: string | null;
}

interface ProgressSsePayload {
  stage?: string;
  progress?: number;
  message?: string;
  final?: boolean;
  error?: string;
}

export interface PdfValidationResult {
  ok: boolean;
  files: File[];
  error?: string;
}

export interface UploadProgressUpdate {
  stage: string;
  progress: number;
  message: string;
  final: boolean;
}

interface WaitForProcessingOptions {
  onProgress?: (update: UploadProgressUpdate) => void;
  signal?: AbortSignal;
  timeoutMs?: number;
  pollingIntervalMs?: number;
}

interface ResolvedWaitForProcessingOptions {
  onProgress: (update: UploadProgressUpdate) => void;
  signal?: AbortSignal;
  pollingIntervalMs: number;
}

const createAbortError = (): Error => {
  try {
    return new DOMException('Operation aborted', 'AbortError');
  } catch (_error) {
    return new Error('Operation aborted');
  }
};

const normalizeStage = (value: string | null | undefined): string => {
  const stage = String(value ?? '').trim().toLowerCase();
  if (!stage) {
    return 'pending';
  }

  if (stage === 'upload') {
    return 'uploading';
  }

  if (stage === 'cancel_requested' || stage === 'cancel-requested') {
    return 'cancelled';
  }

  if (stage === 'canceled') {
    return 'cancelled';
  }

  return stage;
};

const isTerminalStage = (stage: string): boolean => TERMINAL_STAGES.has(stage);

const fallbackProgressForStage = (stage: string): number => {
  return STAGE_PROGRESS_FALLBACK[stage] ?? 0;
};

const clampProgress = (value: unknown, stage: string): number => {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return Math.max(0, Math.min(100, Math.round(value)));
  }
  return fallbackProgressForStage(stage);
};

const defaultMessageForStage = (stage: string): string => {
  switch (stage) {
    case 'uploading':
      return 'Uploading PDF...';
    case 'parsing':
      return 'Parsing PDF...';
    case 'chunking':
      return 'Chunking content...';
    case 'embedding':
      return 'Generating embeddings...';
    case 'storing':
      return 'Storing document...';
    case 'completed':
      return 'Processing completed successfully';
    case 'failed':
      return 'Processing failed';
    case 'cancelled':
      return 'Processing cancelled';
    default:
      return 'Processing document...';
  }
};

const toProgressUpdate = (stageValue: string | null | undefined, progressValue: unknown, messageValue: unknown, final = false): UploadProgressUpdate => {
  const stage = normalizeStage(stageValue);
  const progress = clampProgress(progressValue, stage);
  const message = typeof messageValue === 'string' && messageValue.trim()
    ? messageValue
    : defaultMessageForStage(stage);

  return {
    stage,
    progress,
    message,
    final: final || isTerminalStage(stage),
  };
};

const parseDuplicateUploadMessage = (detail: UploadErrorDetail): string => {
  const uploadDate = detail.uploaded_at
    ? new Date(detail.uploaded_at).toLocaleDateString()
    : 'previously';
  const existingFilename = detail.existing_filename?.trim();
  const suggestion = existingFilename
    ? `The existing document is in Documents as "${existingFilename}". Search for that filename to load it.`
    : detail.suggestion || 'The existing document is still available in Documents.';
  return `This file was already uploaded ${uploadDate}. ${suggestion}`;
};

const parseErrorMessage = (payload: unknown, fallback: string): string => {
  if (!payload || typeof payload !== 'object') {
    return fallback;
  }

  const detail = (payload as { detail?: unknown }).detail;
  if (typeof detail === 'string' && detail.trim()) {
    return detail;
  }

  if (detail && typeof detail === 'object') {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === 'string' && message.trim()) {
      return message;
    }
  }

  return fallback;
};

const wait = async (ms: number, signal?: AbortSignal): Promise<void> => {
  await new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(createAbortError());
      return;
    }
    const timeoutId = window.setTimeout(() => {
      signal?.removeEventListener('abort', handleAbort);
      resolve();
    }, ms);

    const handleAbort = () => {
      window.clearTimeout(timeoutId);
      signal?.removeEventListener('abort', handleAbort);
      reject(createAbortError());
    };

    signal?.addEventListener('abort', handleAbort, { once: true });
  });
};

export const validatePdfSelection = (
  files: File[],
  options: { maxFiles?: number; allowMultiple?: boolean } = {},
): PdfValidationResult => {
  const maxFiles = options.maxFiles ?? PDF_UPLOAD_MAX_SELECTED_FILES;
  const allowMultiple = options.allowMultiple ?? true;

  if (files.length === 0) {
    return {
      ok: false,
      files,
      error: 'Please select a PDF file to upload.',
    };
  }

  if (!allowMultiple && files.length > 1) {
    return {
      ok: false,
      files,
      error: 'Please drop a single PDF file at a time.',
    };
  }

  if (files.length > maxFiles) {
    return {
      ok: false,
      files,
      error: `Please select up to ${maxFiles} PDF files at a time`,
    };
  }

  const invalidFile = files.find((file) => {
    const normalizedType = file.type.toLowerCase();
    const normalizedName = file.name.toLowerCase();
    return normalizedType !== 'application/pdf' && !normalizedName.endsWith('.pdf');
  });

  if (invalidFile) {
    return {
      ok: false,
      files,
      error: 'Please select PDF files only',
    };
  }

  return {
    ok: true,
    files,
  };
};

export const uploadPdfDocument = async (file: File): Promise<string> => {
  const formData = new FormData();
  formData.append('file', file);

  const response = await fetch('/api/weaviate/documents/upload', {
    method: 'POST',
    body: formData,
    credentials: 'include',
  });

  const payload = await response.json().catch(() => ({} as UploadErrorPayload));

  if (!response.ok) {
    if (response.status === 409) {
      const detail = (payload as UploadErrorPayload).detail;
      if (detail && typeof detail === 'object') {
        throw new Error(parseDuplicateUploadMessage(detail));
      }
      throw new Error(parseErrorMessage(payload, 'This file appears to have already been uploaded.'));
    }

    throw new Error(parseErrorMessage(payload, `Upload failed (${response.status})`));
  }

  const result = payload as UploadResponsePayload;
  if (!result.document_id) {
    throw new Error('Upload response did not include a document ID.');
  }

  return result.document_id;
};

const pollDocumentProcessing = async (
  documentId: string,
  options: ResolvedWaitForProcessingOptions,
): Promise<UploadProgressUpdate> => {
  while (true) {
    if (options.signal?.aborted) {
      throw createAbortError();
    }

    const response = await fetch(`/api/weaviate/documents/${documentId}/status`, {
      credentials: 'include',
      signal: options.signal,
    });

    if (!response.ok) {
      throw new Error(`Unable to fetch upload status (${response.status}).`);
    }

    const payload = (await response.json()) as DocumentStatusPayload;
    const stage = payload.pipeline_status?.current_stage ?? payload.processing_status ?? payload.job_status;
    const progress = payload.pipeline_status?.progress_percentage;
    const message = payload.pipeline_status?.message;

    const update = toProgressUpdate(stage, progress, message);
    options.onProgress(update);

    if (isTerminalStage(update.stage)) {
      return update;
    }

    await wait(options.pollingIntervalMs, options.signal);
  }
};

const streamDocumentProcessing = async (
  documentId: string,
  options: ResolvedWaitForProcessingOptions,
): Promise<UploadProgressUpdate> => {
  return await new Promise<UploadProgressUpdate>((resolve, reject) => {
    const source = new EventSource(`/api/weaviate/documents/${documentId}/progress/stream`);
    let hasReceivedMessage = false;

    const connectTimeout = window.setTimeout(() => {
      cleanup();
      reject(new PdfProgressStreamError('transport', 'Unable to connect to upload progress stream.'));
    }, PDF_PROGRESS_CONNECT_TIMEOUT_MS);

    const abortHandler = () => {
      cleanup();
      reject(createAbortError());
    };

    const cleanup = () => {
      window.clearTimeout(connectTimeout);
      source.onopen = null;
      source.onmessage = null;
      source.onerror = null;
      options.signal?.removeEventListener('abort', abortHandler);
      source.close();
    };

    options.signal?.addEventListener('abort', abortHandler, { once: true });

    source.onopen = () => window.clearTimeout(connectTimeout);

    source.onmessage = (event) => {
      hasReceivedMessage = true;
      window.clearTimeout(connectTimeout);

      let parsed: ProgressSsePayload;
      try {
        parsed = parseProgressEvent(event.data);
      } catch (error) {
        cleanup();
        reject(error);
        return;
      }

      const update = toProgressUpdate(parsed.stage, parsed.progress, parsed.message, parsed.final === true);
      options.onProgress(update);

      if (update.final || isTerminalStage(update.stage)) {
        cleanup();
        resolve(update);
      }
    };

    source.onerror = () => {
      cleanup();
      if (!hasReceivedMessage) {
        reject(new PdfProgressStreamError('transport', 'Unable to stream upload progress.'));
        return;
      }
      reject(new PdfProgressStreamError('transport', 'Upload progress stream disconnected.'));
    };
  });
};

export const waitForDocumentProcessing = async (
  documentId: string,
  options: WaitForProcessingOptions = {},
): Promise<UploadProgressUpdate> => {
  const onProgress = options.onProgress ?? (() => undefined);
  const signal = options.signal;
  const timeoutMs = options.timeoutMs ?? PDF_PROGRESS_TIMEOUT_MS;
  const pollingIntervalMs = options.pollingIntervalMs ?? PDF_PROGRESS_POLL_INTERVAL_MS;

  if (!documentId) {
    throw new Error('Document ID is required to track processing progress.');
  }

  if (signal?.aborted) {
    throw createAbortError();
  }

  const controller = new AbortController();
  const abortHandler = () => controller.abort();
  signal?.addEventListener('abort', abortHandler, { once: true });
  let timeoutId: number | undefined;
  const deadline = new Promise<never>((_resolve, reject) => {
    timeoutId = window.setTimeout(() => {
      reject(new Error('Timed out waiting for document processing to complete.'));
      controller.abort();
    }, timeoutMs);
  });
  const typedOptions = { onProgress, signal: controller.signal, pollingIntervalMs };
  const track = async () => {
    if (typeof EventSource !== 'undefined') {
      try {
        return await streamDocumentProcessing(documentId, typedOptions);
      } catch (error) {
        // Removed broad SSE failure fallback — producer-declared and malformed events are terminal; polling is transport-only after ALL-826.
        if (controller.signal.aborted || !(error instanceof PdfProgressStreamError) || error.kind !== 'transport') {
          throw error;
        }
        console.warn('Falling back to status polling after progress stream transport failure.', error);
      }
    }
    return pollDocumentProcessing(documentId, typedOptions);
  };
  try {
    return await Promise.race([track(), deadline]);
  } finally {
    window.clearTimeout(timeoutId);
    signal?.removeEventListener('abort', abortHandler);
    controller.abort();
  }
};

export interface LoadDocumentForChatOptions {
  signal?: AbortSignal;
  intentOwner?: string;
  intentGeneration?: number;
}

export const loadDocumentForChat = async (
  documentId: string,
  options: LoadDocumentForChatOptions = {},
): Promise<Record<string, unknown>> => {
  const response = await fetch('/api/chat/document/load', {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      document_id: documentId,
      intent_owner: options.intentOwner,
      intent_generation: options.intentGeneration,
    }),
    signal: options.signal,
  });

  const payload = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new Error(parseErrorMessage(payload, 'Failed to load document for chat'));
  }

  return payload as Record<string, unknown>;
};

export const dispatchChatDocumentChanged = (
  payload: Record<string, unknown>,
  ownerToken: string = HOME_PDF_VIEWER_OWNER,
) => {
  window.dispatchEvent(new CustomEvent('chat-document-changed', {
    detail: {
      ...payload,
      ownerToken,
    },
  }))
}
import { HOME_PDF_VIEWER_OWNER } from '@/components/pdfViewer/pdfEvents'
