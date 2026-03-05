export const MAX_UPLOAD_FILES_PER_SELECTION = 10;

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

const SSE_CONNECT_TIMEOUT_MS = 5000;

interface UploadErrorDetail {
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
  const suggestion = detail.suggestion || 'Delete the existing document and try again.';
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
  const maxFiles = options.maxFiles ?? MAX_UPLOAD_FILES_PER_SELECTION;
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
  options: Required<Pick<WaitForProcessingOptions, 'onProgress' | 'signal' | 'timeoutMs' | 'pollingIntervalMs'>>,
): Promise<UploadProgressUpdate> => {
  const startedAt = Date.now();

  while (true) {
    if (options.signal?.aborted) {
      throw createAbortError();
    }

    if (Date.now() - startedAt > options.timeoutMs) {
      throw new Error('Timed out waiting for document processing to complete.');
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
  options: Required<Pick<WaitForProcessingOptions, 'onProgress' | 'signal' | 'timeoutMs'>>,
): Promise<UploadProgressUpdate> => {
  return await new Promise<UploadProgressUpdate>((resolve, reject) => {
    const source = new EventSource(`/api/weaviate/documents/${documentId}/progress/stream`);
    let hasReceivedMessage = false;

    const connectTimeout = window.setTimeout(() => {
      cleanup();
      reject(new Error('Unable to connect to upload progress stream.'));
    }, SSE_CONNECT_TIMEOUT_MS);

    const overallTimeout = window.setTimeout(() => {
      cleanup();
      reject(new Error('Timed out waiting for document processing to complete.'));
    }, options.timeoutMs);

    const abortHandler = () => {
      cleanup();
      reject(createAbortError());
    };

    const cleanup = () => {
      window.clearTimeout(connectTimeout);
      window.clearTimeout(overallTimeout);
      options.signal?.removeEventListener('abort', abortHandler);
      source.close();
    };

    options.signal?.addEventListener('abort', abortHandler, { once: true });

    source.onmessage = (event) => {
      hasReceivedMessage = true;
      window.clearTimeout(connectTimeout);

      let parsed: ProgressSsePayload;
      try {
        parsed = JSON.parse(event.data) as ProgressSsePayload;
      } catch (_error) {
        return;
      }

      if (typeof parsed.error === 'string' && parsed.error.trim()) {
        cleanup();
        reject(new Error(parsed.error));
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
        reject(new Error('Unable to stream upload progress.'));
        return;
      }
      reject(new Error('Upload progress stream disconnected.'));
    };
  });
};

export const waitForDocumentProcessing = async (
  documentId: string,
  options: WaitForProcessingOptions = {},
): Promise<UploadProgressUpdate> => {
  const onProgress = options.onProgress ?? (() => undefined);
  const signal = options.signal;
  const timeoutMs = options.timeoutMs ?? 15 * 60 * 1000;
  const pollingIntervalMs = options.pollingIntervalMs ?? 1000;

  if (!documentId) {
    throw new Error('Document ID is required to track processing progress.');
  }

  if (signal?.aborted) {
    throw createAbortError();
  }

  const typedOptions = {
    onProgress,
    signal,
    timeoutMs,
    pollingIntervalMs,
  };

  if (typeof EventSource !== 'undefined') {
    try {
      return await streamDocumentProcessing(documentId, typedOptions);
    } catch (error) {
      if (signal?.aborted) {
        throw error;
      }
      console.warn('Falling back to status polling after progress stream failure.', error);
    }
  }

  return pollDocumentProcessing(documentId, typedOptions);
};

export const loadDocumentForChat = async (documentId: string): Promise<Record<string, unknown>> => {
  const response = await fetch('/api/chat/document/load', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ document_id: documentId }),
  });

  const payload = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new Error(parseErrorMessage(payload, 'Failed to load document for chat'));
  }

  return payload as Record<string, unknown>;
};

export const dispatchChatDocumentChanged = (payload: Record<string, unknown>) => {
  window.dispatchEvent(new CustomEvent('chat-document-changed', { detail: payload }));
};
