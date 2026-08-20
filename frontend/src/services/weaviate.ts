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
import { safeSetItem } from '../lib/browserStorage';

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

interface RawDocumentSourceProvenance {
  provider?: string | null;
  provider_metadata?: RawDocumentSourceProviderMetadata | null;
  reference_id?: string | null;
  reference_curie?: string | null;
  source_file_id?: string | null;
  pdf_artifact_id?: string | null;
  converted_artifact_id?: string | null;
  external_ids?: Record<string, string | string[]> | null;
  source_md5?: string | null;
  file_class?: string | null;
  file_extension?: string | null;
  artifact_status?: string | null;
  import_status?: string | null;
  imported_at?: string | null;
  access_scope?: string | null;
  access_group_ids?: string[] | null;
  viewer_mode?: string | null;
}

interface RawDocumentSourceProviderMetadata {
  display_label?: string | null;
  reference_label_priority?: string[] | null;
  identifier_help_label?: string | null;
  identifier_examples?: string[] | null;
}

interface RawDocumentSourceProviderPresentationResponse {
  provider_id: string;
  presentation?: RawDocumentSourceProviderMetadata | null;
}

interface RawIdentifierImportApiResult {
  identifier: string;
  normalized_identifier?: string | null;
  status: string;
  message?: string | null;
  document_id?: string | null;
  job_id?: string | null;
  filename?: string | null;
  error_code?: string | null;
  existing_document_id?: string | null;
  source_provenance?: Record<string, unknown> | null;
}

interface RawIdentifierImportApiResponse {
  results?: RawIdentifierImportApiResult[];
  requested_count?: number;
  imported_count?: number;
  duplicate_count?: number;
  error_count?: number;
}

interface RawDocumentListItem {
  document_id: string;
  user_id: string;
  filename: string;
  title: string | null;
  status: string;
  upload_timestamp: string | null;
  processing_started_at: string | null;
  processing_completed_at: string | null;
  file_size_bytes: number | null;
  weaviate_tenant: string;
  chunk_count: number | null;
  vector_count: number | null;
  embedding_status: string;
  error_message: string | null;
  source_provenance: RawDocumentSourceProvenance | null;
}

export interface DocumentListResponse {
  documents: RawDocumentListItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface DocumentListData {
  documents: DocumentSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface DocumentListQuery {
  page: number;
  pageSize: number;
  sortBy?: string;
  sortOrder?: 'asc' | 'desc';
  search?: string;
  embeddingStatus?: string[];
  dateFrom?: Date | null;
  dateTo?: Date | null;
  minVectorCount?: number;
  maxVectorCount?: number;
}

export interface DocumentDetailResponse {
  document_id: string;
  job_id: string | null;
  user_id: number;
  filename: string;
  title: string | null;
  status: string;
  upload_timestamp: string;
  processing_started_at: string | null;
  processing_completed_at: string | null;
  file_size_bytes: number;
  weaviate_tenant: string;
  chunk_count: number | null;
  error_message: string | null;
  source_provenance: RawDocumentSourceProvenance | null;
}

export interface DocumentSourceProvenance {
  provider?: string | null;
  providerMetadata?: DocumentSourceProviderMetadata | null;
  referenceId?: string | null;
  referenceCurie?: string | null;
  sourceFileId?: string | null;
  pdfArtifactId?: string | null;
  convertedArtifactId?: string | null;
  externalIds?: Record<string, string | string[]> | null;
  sourceMd5?: string | null;
  fileClass?: string | null;
  fileExtension?: string | null;
  artifactStatus?: string | null;
  importStatus?: string | null;
  importedAt?: string | null;
  accessScope?: string | null;
  accessGroupIds?: string[] | null;
  viewerMode?: string | null;
}

export interface DocumentSourceProviderMetadata {
  displayLabel?: string | null;
  referenceLabelPriority?: string[] | null;
  identifierHelpLabel?: string | null;
  identifierExamples?: string[] | null;
}

export interface ConfiguredDocumentSourcePresentation {
  providerId: string;
  presentation: DocumentSourceProviderMetadata | null;
}

export type LiteratureImportStatus =
  | 'resolved'
  | 'imported'
  | 'duplicate'
  | 'invalid'
  | 'access_denied'
  | 'conversion_running'
  | 'conversion_failed'
  | 'needs_selection'
  | 'no_source_pdf'
  | 'no_converted_text'
  | 'provider_unavailable';

export interface LiteratureImportResult {
  identifier: string;
  normalizedIdentifier: string | null;
  status: LiteratureImportStatus;
  message: string;
  documentId?: string;
  filename?: string;
  jobId?: string;
  source?: {
    provider: string;
    viewerMode: 'local_pdf';
    pdfArtifactId: string;
    convertedArtifactId?: string;
    sourceMd5: string;
  };
}

export interface LiteratureIdentifierBatch {
  results: LiteratureImportResult[];
  requestedCount: number;
  importedCount: number;
  duplicateCount: number;
  errorCount: number;
}

export interface DocumentSummary {
  id: string;
  filename: string;
  title?: string | null;
  fileSize: number | null;
  creationDate: string | null;
  processingStatus: string | null;
  embeddingStatus: string | null;
  errorMessage?: string | null;
  chunkCount: number | null;
  vectorCount: number | null;
  sourceProvenance?: DocumentSourceProvenance | null;
}

export interface DocumentDetailData {
  document: Omit<DocumentSummary, 'embeddingStatus' | 'vectorCount'>;
}

const ACTIVE_DOCUMENT_STATUSES = new Set([
  'processing',
  'parsing',
  'chunking',
  'embedding',
  'storing',
]);

export const isActiveDocumentStatus = (status: string | null | undefined): boolean =>
  ACTIVE_DOCUMENT_STATUSES.has(String(status || '').toLowerCase());

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

const toRecordOrNull = (value: unknown): Record<string, unknown> | null => {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return null;
};

const toStringArrayOrNull = (value: unknown): string[] | null => {
  if (!Array.isArray(value)) {
    return null;
  }

  const normalized = value
    .map((entry) => (typeof entry === 'string' ? entry.trim() : ''))
    .filter(Boolean);
  return normalized.length > 0 ? normalized : null;
};

const toExternalIdsOrNull = (value: unknown): Record<string, string | string[]> | null => {
  const record = toRecordOrNull(value);
  if (!record) {
    return null;
  }

  const normalized = Object.entries(record).reduce<Record<string, string | string[]>>(
    (acc, [key, item]) => {
      if (typeof item === 'string' && item.trim()) {
        acc[key] = item;
      } else if (Array.isArray(item)) {
        const values = item
          .map((entry) => (typeof entry === 'string' ? entry : null))
          .filter((entry): entry is string => Boolean(entry));
        if (values.length > 0) {
          acc[key] = values;
        }
      }
      return acc;
    },
    {}
  );

  return Object.keys(normalized).length > 0 ? normalized : null;
};

const normalizeDocumentSourceProviderMetadata = (
  value: unknown
): DocumentSourceProviderMetadata | null => {
  const record = toRecordOrNull(value);
  if (!record) {
    return null;
  }

  const rawReferenceLabelPriority = record.reference_label_priority;
  const referenceLabelPriority = Array.isArray(rawReferenceLabelPriority)
    ? rawReferenceLabelPriority.filter(
        (entry): entry is string => typeof entry === 'string' && Boolean(entry.trim())
      )
    : null;
  const metadata: DocumentSourceProviderMetadata = {
    displayLabel: toStringOrNull(record.display_label),
    referenceLabelPriority:
      referenceLabelPriority && referenceLabelPriority.length > 0
        ? referenceLabelPriority
        : null,
    identifierHelpLabel: toStringOrNull(record.identifier_help_label),
    identifierExamples: toStringArrayOrNull(record.identifier_examples),
  };

  return metadata.displayLabel
    || metadata.referenceLabelPriority
    || metadata.identifierHelpLabel
    || metadata.identifierExamples
    ? metadata
    : null;
};

export const normalizeDocumentSourceProvenance = (
  raw: RawDocumentSourceProvenance | null | undefined,
): DocumentSourceProvenance | null => {
  if (!raw) {
    return null;
  }

  const normalized: DocumentSourceProvenance = {
    provider: toStringOrNull(raw.provider),
    providerMetadata:
      normalizeDocumentSourceProviderMetadata(raw.provider_metadata),
    referenceId: toStringOrNull(raw.reference_id),
    referenceCurie: toStringOrNull(raw.reference_curie),
    sourceFileId: toStringOrNull(raw.source_file_id),
    pdfArtifactId: toStringOrNull(raw.pdf_artifact_id),
    convertedArtifactId: toStringOrNull(raw.converted_artifact_id),
    externalIds: toExternalIdsOrNull(raw.external_ids),
    sourceMd5: toStringOrNull(raw.source_md5),
    fileClass: toStringOrNull(raw.file_class),
    fileExtension: toStringOrNull(raw.file_extension),
    artifactStatus: toStringOrNull(raw.artifact_status),
    importStatus: toStringOrNull(raw.import_status),
    importedAt: toStringOrNull(raw.imported_at),
    accessScope: toStringOrNull(raw.access_scope),
    accessGroupIds: toStringArrayOrNull(raw.access_group_ids),
    viewerMode: toStringOrNull(raw.viewer_mode),
  };

  return normalized.provider ? normalized : null;
};

export const normalizeDocumentListResponse = (
  response: DocumentListResponse
): DocumentListData => ({
  documents: response.documents.map((document) => ({
    id: document.document_id,
    filename: document.filename,
    title: document.title,
    fileSize: document.file_size_bytes,
    creationDate: document.upload_timestamp,
    processingStatus: document.status.toLowerCase(),
    embeddingStatus: document.embedding_status,
    errorMessage: document.error_message,
    chunkCount: document.chunk_count,
    vectorCount: document.vector_count,
    sourceProvenance: normalizeDocumentSourceProvenance(document.source_provenance),
  })),
  total: response.total,
  limit: response.limit,
  offset: response.offset,
});

export const normalizeDocumentDetailResponse = (
  payload: DocumentDetailResponse
): DocumentDetailData => {
  return {
    document: {
      id: payload.document_id,
      filename: payload.filename,
      title: payload.title,
      fileSize: payload.file_size_bytes,
      creationDate: payload.upload_timestamp,
      processingStatus: payload.status.toLowerCase(),
      errorMessage: payload.error_message,
      chunkCount: payload.chunk_count,
      sourceProvenance: normalizeDocumentSourceProvenance(payload.source_provenance),
    },
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
        safeSetItem(() => window.sessionStorage, 'intendedPath', window.location.pathname + window.location.search, {
          owner: 'auth',
          key: 'intendedPath',
          workflowCritical: true,
        });

        // Redirect to login endpoint (backend will redirect to Cognito)
        window.location.href = '/api/auth/login';

        // Throw error for any pending promises (though redirect will interrupt execution)
        throw new AuthenticationError('Session expired - redirecting to login');
      }

      const error = await response.json().catch(() => ({
        message: `HTTP error! status: ${response.status}`,
      }));

      const detailMessage = typeof error.detail === 'string'
        ? error.detail
        : error.detail && typeof error.detail === 'object' && typeof error.detail.message === 'string'
          ? error.detail.message
          : null;
      const errorMessage = error.message || detailMessage || `Failed to fetch ${path}`;

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
    const isAbortError = typeof error === 'object' && error !== null &&
      'name' in error && error.name === 'AbortError';
    if (!isAbortError) {
      // Log network or parsing errors. Request cancellation is expected control flow.
      logger.error('API request failed', error as Error, {
        component: 'weaviate-service',
        action: 'fetchApi',
        metadata: {
          url,
          method,
        },
      });
    }
    throw error;
  }
};

export const buildDocumentListSearchParams = (
  query: DocumentListQuery
): URLSearchParams => {
  const queryParams = new URLSearchParams({
    page: String(query.page + 1),
    page_size: String(query.pageSize),
  });
  if (query.sortBy) queryParams.set('sort_by', query.sortBy);
  if (query.sortOrder) queryParams.set('sort_order', query.sortOrder);
  if (query.search) queryParams.set('search', query.search);
  query.embeddingStatus?.forEach((status) => queryParams.append('embedding_status', status));
  if (query.dateFrom) queryParams.set('date_from', query.dateFrom.toISOString());
  if (query.dateTo) queryParams.set('date_to', query.dateTo.toISOString());
  if (query.minVectorCount !== undefined) {
    queryParams.set('min_vector_count', String(query.minVectorCount));
  }
  if (query.maxVectorCount !== undefined) {
    queryParams.set('max_vector_count', String(query.maxVectorCount));
  }
  return queryParams;
};

export const fetchDocumentList = async (
  query: DocumentListQuery,
  options?: RequestInit
): Promise<DocumentListData> => {
  const queryParams = buildDocumentListSearchParams(query);
  const response = await fetchApi<DocumentListResponse>(
    `/documents?${queryParams.toString()}`,
    options
  );
  return normalizeDocumentListResponse(response);
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
  options?: Omit<
    UseQueryOptions<PdfExtractionHealthStatus, Error, PdfExtractionHealthStatus, QueryKey>,
    'queryKey' | 'queryFn'
  >
) =>
  useQuery<PdfExtractionHealthStatus>({
    queryKey: ['pdf-extraction-health'],
    queryFn: fetchPdfExtractionHealth,
    refetchInterval: 60_000,
    retry: false,
    ...options,
  });

const identifierStatusFromApiResult = (
  result: RawIdentifierImportApiResult,
): LiteratureImportStatus => {
  if (result.status === 'resolved' || result.status === 'imported' || result.status === 'duplicate') {
    return result.status;
  }

  // The backend contract uses document_source_* errors; legacy short aliases are intentionally not mapped.
  switch (result.error_code) {
    case 'document_source_access_denied':
      return 'access_denied';
    case 'document_source_unavailable':
    case 'document_source_import_unavailable':
    case 'document_source_curator_token_unavailable':
      return 'provider_unavailable';
    case 'document_source_conversion_running':
      return 'conversion_running';
    case 'document_source_conversion_failed':
      return 'conversion_failed';
    case 'document_source_ambiguous_match':
      return 'needs_selection';
    case 'document_source_no_source_artifact':
      return 'no_source_pdf';
    case 'document_source_no_converted_text':
      return 'no_converted_text';
    default:
      return 'invalid';
  }
};

const nonEmptyRecordString = (
  record: Record<string, unknown>,
  key: string,
): string | undefined => {
  const value = record[key];
  return typeof value === 'string' && value.trim() ? value : undefined;
};

const normalizeIdentifierImportResult = (
  result: RawIdentifierImportApiResult,
): LiteratureImportResult => {
  const provenance = result.source_provenance ?? undefined;
  const viewerMode = provenance
    ? nonEmptyRecordString(provenance, 'viewer_mode')
    : undefined;
  // Canonical provenance is artifact-based; retired *_referencefile_id aliases are intentionally ignored.
  const pdfArtifactId = provenance
    ? nonEmptyRecordString(provenance, 'pdf_artifact_id')
    : undefined;

  return {
    identifier: result.identifier,
    normalizedIdentifier: result.normalized_identifier ?? null,
    status: identifierStatusFromApiResult(result),
    message: result.message || 'Source identifier returned without a message.',
    documentId: result.document_id ?? result.existing_document_id ?? undefined,
    filename: result.filename ?? undefined,
    jobId: result.job_id ?? undefined,
    source: viewerMode === 'local_pdf' && pdfArtifactId
      ? {
          provider: nonEmptyRecordString(provenance!, 'provider') ?? 'document_source',
          viewerMode: 'local_pdf',
          pdfArtifactId,
          convertedArtifactId: nonEmptyRecordString(provenance!, 'converted_artifact_id'),
          sourceMd5: nonEmptyRecordString(provenance!, 'source_md5') ?? 'unknown',
        }
      : undefined,
  };
};

const postIdentifierBatch = async (
  endpoint: string,
  rawIdentifiers: string,
): Promise<LiteratureIdentifierBatch> => {
  const payload = await fetchApi<RawIdentifierImportApiResponse>(endpoint, {
    method: 'POST',
    body: JSON.stringify({ identifiers: rawIdentifiers }),
  });

  return {
    results: (payload.results ?? []).map(normalizeIdentifierImportResult),
    requestedCount: payload.requested_count ?? 0,
    importedCount: payload.imported_count ?? 0,
    duplicateCount: payload.duplicate_count ?? 0,
    errorCount: payload.error_count ?? 0,
  };
};

export const resolveSourceIdentifiers = (
  rawIdentifiers: string,
): Promise<LiteratureIdentifierBatch> => (
  postIdentifierBatch('/documents/resolve/source-identifiers', rawIdentifiers)
);

export const importSourceIdentifiers = (
  rawIdentifiers: string,
): Promise<LiteratureIdentifierBatch> => (
  postIdentifierBatch('/documents/import/source-identifiers', rawIdentifiers)
);

export const fetchConfiguredDocumentSourcePresentation = async (
): Promise<ConfiguredDocumentSourcePresentation> => {
  const payload = await fetchApi<RawDocumentSourceProviderPresentationResponse>(
    '/document-source/provider-presentation',
  );
  return {
    providerId: payload.provider_id,
    presentation: normalizeDocumentSourceProviderMetadata(payload.presentation),
  };
};

export interface PdfJobsQuery {
  status?: PdfJobStatus[];
  windowDays?: number;
  limit?: number;
  offset?: number;
}

const buildPdfJobsSearchParams = (params: PdfJobsQuery): URLSearchParams => {
  const query = new URLSearchParams();
  (params.status ?? []).forEach((statusValue) => query.append('status', statusValue));
  if (params.windowDays !== undefined) query.set('window_days', String(params.windowDays));
  if (params.limit !== undefined) query.set('limit', String(params.limit));
  if (params.offset !== undefined) query.set('offset', String(params.offset));
  return query;
};

export const buildPdfJobsStreamUrl = (params: PdfJobsQuery = {}): string => {
  const query = buildPdfJobsSearchParams(params);
  const suffix = query.toString() ? `?${query.toString()}` : '';
  return `${API_BASE_URL}/pdf-jobs/stream${suffix}`;
};

export const fetchPdfJobs = async (
  params: PdfJobsQuery = {}
): Promise<PdfJobListResponse> => {
  const query = buildPdfJobsSearchParams(params);
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

export const fetchDocumentDetail = async (id: string): Promise<DocumentDetailData> => {
  const payload = await fetchApi<DocumentDetailResponse>(`/documents/${id}`);
  return normalizeDocumentDetailResponse(payload);
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
