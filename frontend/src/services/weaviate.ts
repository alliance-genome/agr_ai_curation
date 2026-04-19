import {
  useQuery,
  useMutation,
  useQueryClient,
  UseQueryOptions,
  UseMutationOptions,
  QueryKey,
} from '@tanstack/react-query';
import { logger } from './logger';
import { clearAllNamespacedChatLocalStorage } from '../lib/chatCacheKeys';

const API_BASE_URL = '/api/weaviate';

/**
 * Custom error class for authentication failures (401)
 * This allows us to specifically handle auth errors differently from other API errors
 */
export class AuthenticationError extends Error {
  constructor(message: string = 'Authentication required') {
    super(message);
    this.name = 'AuthenticationError';
  }
}

export interface PDFDocument {
  id: string;
  filename: string;
  fileSize: number;
  creationDate: Date;
  lastAccessedDate: Date;
  processingStatus: string;
  embeddingStatus: string;
  chunkCount: number;
  vectorCount: number;
  metadata: {
    pageCount?: number;
    author?: string;
    title?: string;
    checksum: string;
    documentType: string;
    lastProcessedStage: string;
  };
}

interface DocumentChunk {
  id: string;
  documentId: string;
  chunkIndex: number;
  content: string;
  elementType: string;
  pageNumber: number;
  sectionTitle?: string;
  metadata: {
    characterCount: number;
    wordCount: number;
    hasTable: boolean;
    hasImage: boolean;
  };
}

export interface DocumentListResponse {
  documents: PDFDocument[];
  pagination: {
    currentPage: number;
    totalPages: number;
    totalItems: number;
    pageSize: number;
  };
  filters: DocumentFilter;
}

export interface RawDocumentDetailResponse {
  document: Record<string, unknown>;
  chunks?: Array<Record<string, unknown>>;
  chunks_preview?: Array<Record<string, unknown>>;
  total_chunks?: number;
  embedding_summary?: Record<string, unknown>;
  embeddings?: Record<string, unknown>;
  pipeline_status?: Record<string, unknown> | null;
  related_documents?: Array<Record<string, unknown>>;
  schema_version?: string;
}

export interface DocumentSummary {
  id: string;
  filename: string;
  title?: string | null;
  fileSize: number | null;
  creationDate: string | null;
  lastAccessedDate: string | null;
  processingStatus: string | null;
  embeddingStatus: string | null;
  chunkCount: number | null;
  vectorCount: number | null;
  metadata?: Record<string, unknown> | null;
}

export interface EmbeddingModelBreakdown {
  name: string;
  chunkCount: number;
}

export interface EmbeddingSummary {
  totalChunks: number;
  embeddedChunks: number;
  coveragePercentage?: number | null;
  lastEmbeddedAt?: string | null;
  primaryModel?: string | null;
  models: EmbeddingModelBreakdown[];
}

export interface PipelineStatusSummary {
  currentStage?: string | null;
  progressPercentage?: number | null;
  message?: string | null;
  startedAt?: string | null;
  updatedAt?: string | null;
  completedAt?: string | null;
  errorCount?: number | null;
}

export interface ChunkPreviewSummary {
  id: string;
  chunkIndex?: number | null;
  content: string;
  pageNumber?: number | null;
  elementType?: string | null;
  sectionTitle?: string | null;
  metadata?: Record<string, unknown> | null;
  embeddingModel?: string | null;
  embeddingTimestamp?: string | null;
}

export interface DocumentDetailData {
  document: DocumentSummary;
  embeddingSummary?: EmbeddingSummary;
  pipelineStatus?: PipelineStatusSummary;
  chunksPreview: ChunkPreviewSummary[];
  totalChunks: number;
  relatedDocuments: DocumentSummary[];
  schemaVersion?: string;
}

export interface PdfExtractionHealthStatus {
  status: 'healthy' | 'degraded' | 'unreachable' | 'misconfigured' | 'unknown';
  service_url: string;
  last_checked?: string;
  response_code?: number;
  details?: Record<string, unknown> | null;
  deep_details?: Record<string, unknown> | null;
  deep_response_code?: number;
  worker_state?: string;
  worker_available?: boolean;
  wake_required?: boolean;
  status_details?: Record<string, unknown> | null;
  status_response_code?: number;
  status_error?: string;
  error?: string;
}

export interface PdfExtractionWakeResponse {
  service_url: string;
  wake_response_code: number;
  wake_details?: Record<string, unknown> | null;
  status_response_code?: number;
  status_details?: Record<string, unknown> | null;
  worker_state?: string;
  worker_available?: boolean;
  wake_required?: boolean;
}

export type PdfJobStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancel_requested'
  | 'cancelled';

export interface PdfProcessingJob {
  job_id: string;
  document_id: string;
  user_id: number;
  filename?: string | null;
  status: PdfJobStatus;
  current_stage?: string | null;
  progress_percentage: number;
  message?: string | null;
  process_id?: string | null;
  cancel_requested: boolean;
  error_message?: string | null;
  metadata?: Record<string, unknown> | null;
  created_at: string;
  started_at?: string | null;
  updated_at: string;
  completed_at?: string | null;
}

export interface PdfJobListResponse {
  jobs: PdfProcessingJob[];
  total: number;
  limit: number;
  offset: number;
}

export interface CancelPdfJobResponse {
  success: boolean;
  message: string;
  job: PdfProcessingJob;
}

const toStringOrNull = (value: unknown): string | null => {
  if (typeof value === 'string') {
    return value;
  }
  if (value instanceof Date) {
    return value.toISOString();
  }
  return null;
};

const normalizeDocumentSummary = (
  raw: Record<string, unknown> | undefined,
  fallback?: DocumentSummary,
  fallbackId?: string
): DocumentSummary => {
  const metadata = (raw?.metadata ?? raw?.['metadata']) as Record<string, unknown> | undefined;

  return {
    id: String(raw?.id ?? fallback?.id ?? fallbackId ?? ''),
    filename: String(raw?.filename ?? fallback?.filename ?? 'Untitled'),
    fileSize: (raw?.file_size ?? raw?.fileSize ?? fallback?.fileSize ?? null) as number | null,
    creationDate: toStringOrNull(
      raw?.creation_date ?? raw?.creationDate ?? fallback?.creationDate ?? null
    ),
    lastAccessedDate: toStringOrNull(
      raw?.last_accessed_date ?? raw?.lastAccessedDate ?? fallback?.lastAccessedDate ?? null
    ),
    processingStatus: (raw?.processing_status ?? raw?.processingStatus ?? fallback?.processingStatus ?? null) as string | null,
    embeddingStatus: (raw?.embedding_status ?? raw?.embeddingStatus ?? fallback?.embeddingStatus ?? null) as string | null,
    chunkCount: (raw?.chunk_count ?? raw?.chunkCount ?? fallback?.chunkCount ?? null) as number | null,
    vectorCount: (raw?.vector_count ?? raw?.vectorCount ?? fallback?.vectorCount ?? null) as number | null,
    metadata: metadata ?? fallback?.metadata ?? null,
  };
};

const normalizeEmbeddingSummary = (
  raw: Record<string, unknown> | undefined,
  defaults?: { totalChunks?: number; embeddedChunks?: number }
): EmbeddingSummary | undefined => {
  if (!raw && !defaults) {
    return undefined;
  }

  const modelsRaw = Array.isArray(raw?.models) ? (raw?.models as Array<Record<string, unknown>>) : [];
  const normalizedDefaults = {
    totalChunks: defaults?.totalChunks ?? 0,
    embeddedChunks: defaults?.embeddedChunks ?? 0,
  };

  const totalChunks = (raw?.total_chunks ?? raw?.totalChunks ?? normalizedDefaults.totalChunks) as number;
  const embeddedChunks = (raw?.embedded_chunks ?? raw?.embeddedChunks ?? normalizedDefaults.embeddedChunks) as number;

  return {
    totalChunks,
    embeddedChunks,
    coveragePercentage: (raw?.coverage_percentage ?? raw?.coveragePercentage ?? null) as number | null,
    lastEmbeddedAt: toStringOrNull(raw?.last_embedded_at ?? raw?.lastEmbeddedAt ?? null),
    primaryModel: (raw?.primary_model ?? raw?.primaryModel ?? null) as string | null,
    models: modelsRaw.map((model) => ({
      name: String(model.model ?? model.name ?? 'unknown'),
      chunkCount: (model.chunk_count ?? model.chunkCount ?? 0) as number,
    })),
  };
};

const normalizePipelineStatus = (
  raw: Record<string, unknown> | null | undefined
): PipelineStatusSummary | undefined => {
  if (!raw) {
    return undefined;
  }

  return {
    currentStage: (raw.current_stage ?? raw.currentStage ?? null) as string | null,
    progressPercentage: (raw.progress_percentage ?? raw.progressPercentage ?? null) as number | null,
    message: (raw.message ?? null) as string | null,
    startedAt: toStringOrNull(raw.started_at ?? raw.startedAt ?? null),
    updatedAt: toStringOrNull(raw.updated_at ?? raw.updatedAt ?? null),
    completedAt: toStringOrNull(raw.completed_at ?? raw.completedAt ?? null),
    errorCount: (raw.error_count ?? raw.errorCount ?? null) as number | null,
  };
};

const normalizeChunkPreviews = (
  chunks: Array<Record<string, unknown>> | undefined,
  documentId?: string
): ChunkPreviewSummary[] => {
  if (!Array.isArray(chunks) || chunks.length === 0) {
    return [];
  }

  return chunks.map((chunk, index) => ({
    id: String(chunk.id ?? `${documentId ?? 'doc'}-chunk-${index}`),
    chunkIndex: (chunk.chunk_index ?? chunk.chunkIndex ?? index) as number,
    content: String(chunk.content ?? ''),
    pageNumber: (chunk.page_number ?? chunk.pageNumber ?? null) as number | null,
    elementType: (chunk.element_type ?? chunk.elementType ?? null) as string | null,
    sectionTitle: (chunk.section_title ?? chunk.sectionTitle ?? null) as string | null,
    metadata: (chunk.metadata ?? null) as Record<string, unknown> | null,
    embeddingModel: (chunk.embedding_model ?? chunk.embeddingModel ?? null) as string | null,
    embeddingTimestamp: toStringOrNull(chunk.embedding_timestamp ?? chunk.embeddingTimestamp ?? null),
  }));
};

const normalizeRelatedDocuments = (
  docs: Array<Record<string, unknown>> | undefined
): DocumentSummary[] => {
  if (!Array.isArray(docs)) {
    return [];
  }

  return docs.map((doc) => normalizeDocumentSummary(doc));
};

export interface NormalizeDocumentDetailOptions {
  fallbackSummary?: DocumentSummary;
  documentId?: string;
}

export const normalizeDocumentDetailResponse = (
  payload: RawDocumentDetailResponse,
  options: NormalizeDocumentDetailOptions = {}
): DocumentDetailData => {
  const { fallbackSummary, documentId } = options;
  const document = normalizeDocumentSummary(payload.document, fallbackSummary, documentId);
  const totalChunks = (payload.total_chunks ?? fallbackSummary?.chunkCount ?? 0) as number;
  const embeddedChunks = document.vectorCount ?? 0;

  const embeddingSummary =
    normalizeEmbeddingSummary(payload.embedding_summary, {
      totalChunks,
      embeddedChunks,
    }) ??
    normalizeEmbeddingSummary(payload.embeddings as Record<string, unknown> | undefined, {
      totalChunks,
      embeddedChunks,
    });

  const chunksPreview = normalizeChunkPreviews(
    payload.chunks_preview ?? payload.chunks,
    document.id
  );

  return {
    document,
    embeddingSummary,
    pipelineStatus: normalizePipelineStatus(payload.pipeline_status),
    chunksPreview,
    totalChunks,
    relatedDocuments: normalizeRelatedDocuments(payload.related_documents),
    schemaVersion: payload.schema_version ?? undefined,
  };
};

export interface DocumentFilter {
  searchTerm?: string;
  embeddingStatus?: string[];
  dateFrom?: Date | null;
  dateTo?: Date | null;
  minVectorCount?: number;
  maxVectorCount?: number;
}

interface PaginationParams {
  page: number;
  pageSize: number;
  sortBy?: string;
  sortOrder?: 'asc' | 'desc';
}

interface ChunkingStrategy {
  strategyName: string;
  chunkingMethod: string;
  maxCharacters: number;
  overlapCharacters: number;
  includeMetadata: boolean;
  excludeElementTypes: string[];
}

interface EmbeddingConfiguration {
  modelProvider: string;
  modelName: string;
  dimensions: number;
  batchSize: number;
}

interface WeaviateSettings {
  collectionName: string;
  schemaVersion: string;
  replicationFactor: number;
  consistency: string;
  vectorIndexType: string;
}

interface SettingsResponse {
  embedding: EmbeddingConfiguration;
  database: WeaviateSettings;
  availableModels: {
    provider: string;
    models: Array<{ name: string; dimensions: number }>;
  }[];
}

export const fetchApi = async <T>(
  path: string,
  options?: RequestInit
): Promise<T> => {
  const url = `${API_BASE_URL}${path}`;
  const method = options?.method || 'GET';

  // Start API call logging
  logger.logApiCall(method, url);

  try {
    const response = await fetch(url, {
      ...options,
      credentials: 'include', // Include httpOnly cookies for authentication
      headers: {
        'Content-Type': 'application/json',
        ...options?.headers,
      },
    });

    if (!response.ok) {
      // Special handling for 401 Unauthorized - session expired or invalid
      if (response.status === 401) {
        logger.warn('Authentication required - session expired, redirecting to login', {
          component: 'weaviate-service',
          action: 'fetchApi',
          metadata: {
            url,
            method,
            status: 401,
          },
        });

        // Clear auth-bound browser state before redirecting to login.
        clearAllNamespacedChatLocalStorage();
        sessionStorage.setItem('intendedPath', window.location.pathname + window.location.search);

        // Redirect to login endpoint (backend will redirect to Cognito)
        window.location.href = '/api/auth/login';

        // Throw error for any pending promises (though redirect will interrupt execution)
        throw new AuthenticationError('Session expired - redirecting to login');
      }

      const error = await response.json().catch(() => ({
        message: `HTTP error! status: ${response.status}`,
      }));

      const errorMessage = error.message || `Failed to fetch ${path}`;

      // Log API error
      logger.error('API request failed', new Error(errorMessage), {
        component: 'weaviate-service',
        action: 'fetchApi',
        metadata: {
          url,
          method,
          status: response.status,
          error: error,
        },
      });

      throw new Error(errorMessage);
    }

    const data = await response.json();

    // Log successful API response
    logger.debug('API request successful', {
      component: 'weaviate-service',
      action: 'fetchApi',
      metadata: {
        url,
        method,
        status: response.status,
      },
    });

    return data;
  } catch (error) {
    // Log network or parsing errors
    logger.error('API request failed', error as Error, {
      component: 'weaviate-service',
      action: 'fetchApi',
      metadata: {
        url,
        method,
      },
    });
    throw error;
  }
};

const fetchPdfExtractionHealth = async (): Promise<PdfExtractionHealthStatus> => {
  const response = await fetch(`${API_BASE_URL}/documents/pdf-extraction-health`, {
    credentials: 'include', // Include httpOnly cookies for authentication
  });
  if (!response.ok) {
    throw new Error('Failed to fetch PDF extraction service health');
  }

  const data = (await response.json()) as PdfExtractionHealthStatus;
  return {
    status: (data?.status as PdfExtractionHealthStatus['status']) ?? 'unknown',
    service_url: data?.service_url ?? '',
    last_checked: data?.last_checked,
    response_code: data?.response_code,
    details: data?.details ?? null,
    deep_details: data?.deep_details ?? null,
    deep_response_code: data?.deep_response_code,
    worker_state: data?.worker_state,
    worker_available: data?.worker_available,
    wake_required: data?.wake_required,
    status_details: data?.status_details ?? null,
    status_response_code: data?.status_response_code,
    status_error: data?.status_error,
    error: data?.error,
  };
};

export const wakePdfExtractionWorker = async (): Promise<PdfExtractionWakeResponse> => {
  const response = await fetch(`${API_BASE_URL}/documents/pdf-extraction-wake`, {
    method: 'POST',
    credentials: 'include',
  });

  if (!response.ok) {
    let message = 'Failed to wake PDF extraction worker';
    try {
      const payload = await response.json();
      const detail = payload?.detail;
      if (typeof detail === 'string') {
        message = detail;
      } else if (detail && typeof detail === 'object' && typeof detail.message === 'string') {
        message = detail.message;
      }
    } catch {
      // Keep default message
    }
    throw new Error(message);
  }

  const data = (await response.json()) as PdfExtractionWakeResponse;
  return data;
};

export const usePdfExtractionHealth = (
  options?: UseQueryOptions<PdfExtractionHealthStatus>
) =>
  useQuery<PdfExtractionHealthStatus>({
    queryKey: ['pdf-extraction-health'],
    queryFn: fetchPdfExtractionHealth,
    refetchInterval: 60_000,
    retry: false,
    ...options,
  });

export const fetchPdfJobs = async (
  params: {
    status?: PdfJobStatus[];
    windowDays?: number;
    limit?: number;
    offset?: number;
  } = {}
): Promise<PdfJobListResponse> => {
  const query = new URLSearchParams();
  (params.status ?? []).forEach((statusValue) => query.append('status', statusValue));
  if (params.windowDays) query.set('window_days', String(params.windowDays));
  if (params.limit) query.set('limit', String(params.limit));
  if (params.offset) query.set('offset', String(params.offset));
  const suffix = query.toString() ? `?${query.toString()}` : '';
  return fetchApi<PdfJobListResponse>(`/pdf-jobs${suffix}`);
};

export const fetchPdfJob = async (jobId: string): Promise<PdfProcessingJob> => {
  return fetchApi<PdfProcessingJob>(`/pdf-jobs/${jobId}`);
};

export const cancelPdfJob = async (jobId: string): Promise<CancelPdfJobResponse> => {
  return fetchApi<CancelPdfJobResponse>(`/pdf-jobs/${jobId}/cancel`, { method: 'POST' });
};

export const usePdfJobs = (
  params: {
    status?: PdfJobStatus[];
    windowDays?: number;
    limit?: number;
    offset?: number;
  } = {},
  options?: UseQueryOptions<PdfJobListResponse>
) =>
  useQuery<PdfJobListResponse>({
    queryKey: ['pdf-jobs', params],
    queryFn: () => fetchPdfJobs(params),
    refetchInterval: 15_000,
    ...options,
  });

export const useCancelPdfJob = (
  options?: UseMutationOptions<CancelPdfJobResponse, Error, string>
) => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (jobId: string) => cancelPdfJob(jobId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pdf-jobs'] });
      queryClient.invalidateQueries({ queryKey: ['documents'] });
    },
    ...options,
  });
};

// Query hooks
export const useDocuments = (
  filters: DocumentFilter,
  pagination: PaginationParams,
  options?: UseQueryOptions<DocumentListResponse>
) => {
  const queryParams = new URLSearchParams({
    page: pagination.page.toString(),
    pageSize: pagination.pageSize.toString(),
    ...(pagination.sortBy && { sortBy: pagination.sortBy }),
    ...(pagination.sortOrder && { sortOrder: pagination.sortOrder }),
    ...(filters.searchTerm && { search: filters.searchTerm }),
    ...(filters.embeddingStatus && {
      status: filters.embeddingStatus.join(','),
    }),
    ...(filters.dateFrom && {
      dateFrom: filters.dateFrom.toISOString(),
    }),
    ...(filters.dateTo && { dateTo: filters.dateTo.toISOString() }),
    ...(filters.minVectorCount !== undefined && {
      min_vector_count: filters.minVectorCount.toString(),
    }),
    ...(filters.maxVectorCount !== undefined && {
      max_vector_count: filters.maxVectorCount.toString(),
    }),
  });

  return useQuery({
    queryKey: ['documents', filters, pagination],
    queryFn: () => fetchApi<DocumentListResponse>(`/documents?${queryParams}`),
    ...options,
  });
};

export const fetchDocumentDetail = async (id: string): Promise<DocumentDetailData> => {
  const payload = await fetchApi<RawDocumentDetailResponse>(`/documents/${id}`);
  return normalizeDocumentDetailResponse(payload, { documentId: id });
};

export const useDocument = (
  id: string,
  options?: Omit<UseQueryOptions<DocumentDetailData, Error, DocumentDetailData, QueryKey>, 'queryKey' | 'queryFn'>
) => {
  return useQuery({
    queryKey: ['document', id],
    queryFn: () => fetchDocumentDetail(id),
    enabled: !!id,
    ...options,
  });
};

export const useDocumentChunks = (
  documentId: string,
  page: number = 1,
  pageSize: number = 20,
  options?: UseQueryOptions<{
    chunks: DocumentChunk[];
    totalCount: number;
  }>
) => {
  return useQuery({
    queryKey: ['documentChunks', documentId, page, pageSize],
    queryFn: () =>
      fetchApi<{ chunks: DocumentChunk[]; totalCount: number }>(
        `/documents/${documentId}/chunks?page=${page}&pageSize=${pageSize}`
      ),
    enabled: !!documentId,
    ...options,
  });
};

export const useWeaviateSettings = (
  options?: UseQueryOptions<SettingsResponse>
) => {
  return useQuery({
    queryKey: ['weaviateSettings'],
    queryFn: () => fetchApi<SettingsResponse>('/settings'),
    ...options,
  });
};

// Mutation hooks
export const useDeleteDocument = (
  options?: UseMutationOptions<void, Error, string>
) => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: string) =>
      fetchApi<void>(`/documents/${id}`, {
        method: 'DELETE',
      }),
    onSuccess: (_, id) => {
      queryClient.invalidateQueries({ queryKey: ['documents'] });
      queryClient.removeQueries({ queryKey: ['document', id] });
    },
    ...options,
  });
};

export const useReembedDocument = (
  options?: UseMutationOptions<PDFDocument, Error, string>
) => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: string) =>
      fetchApi<PDFDocument>(`/documents/${id}/reembed`, {
        method: 'POST',
      }),
    onSuccess: (_data, id) => {
      queryClient.invalidateQueries({ queryKey: ['documents'] });
      queryClient.invalidateQueries({ queryKey: ['document', id] });
    },
    ...options,
  });
};

export const useReprocessDocument = (
  options?: UseMutationOptions<PDFDocument, Error, string>
) => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: string) =>
      fetchApi<PDFDocument>(`/documents/${id}/reprocess`, {
        method: 'POST',
      }),
    onSuccess: (_data, id) => {
      queryClient.invalidateQueries({ queryKey: ['documents'] });
      queryClient.invalidateQueries({ queryKey: ['document', id] });
    },
    ...options,
  });
};

export const useUpdateEmbeddingSettings = (
  options?: UseMutationOptions<void, Error, EmbeddingConfiguration>
) => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (config: EmbeddingConfiguration) =>
      fetchApi<void>('/settings/embedding', {
        method: 'PUT',
        body: JSON.stringify(config),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['weaviateSettings'] });
    },
    ...options,
  });
};

export const useUpdateWeaviateSettings = (
  options?: UseMutationOptions<void, Error, WeaviateSettings>
) => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (settings: WeaviateSettings) =>
      fetchApi<void>('/settings/database', {
        method: 'PUT',
        body: JSON.stringify(settings),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['weaviateSettings'] });
    },
    ...options,
  });
};

export const useUpdateChunkingStrategy = (
  options?: UseMutationOptions<void, Error, ChunkingStrategy>
) => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (strategy: ChunkingStrategy) =>
      fetchApi<void>('/settings/chunking', {
        method: 'PUT',
        body: JSON.stringify(strategy),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['weaviateSettings'] });
    },
    ...options,
  });
};

// Health check
export const useWeaviateHealth = (
  options?: UseQueryOptions<{ status: string; message: string }>
) => {
  return useQuery({
    queryKey: ['weaviateHealth'],
    queryFn: () =>
      fetchApi<{ status: string; message: string }>('/health'),
    refetchInterval: 30000, // Check every 30 seconds
    ...options,
  });
};
