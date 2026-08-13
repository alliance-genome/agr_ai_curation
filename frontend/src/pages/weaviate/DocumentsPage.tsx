import React, { Suspense, lazy, useState, useEffect, useMemo, useRef } from 'react';
import { Alert, Box, Button, Paper, Snackbar, Typography } from '@mui/material';
import { PlaylistPlay as BatchIcon } from '@mui/icons-material';
import { useNavigate } from 'react-router-dom';
import type { GridPaginationModel, GridSortModel } from '@mui/x-data-grid';
import { normalizeDocumentSourceProvenance } from '../../services/weaviate';
import type {
  DocumentSummary,
  DocumentListResponse,
  DocumentFilter,
} from '../../services/weaviate';

const DocumentList = lazy(() => import('../../components/weaviate/DocumentList'));
const InlineFilterBar = lazy(() => import('../../components/weaviate/InlineFilterBar'));

const readString = (value: unknown, defaultValue: string): string => (
  typeof value === 'string' && value.trim() ? value : defaultValue
);

const readNullableString = (value: unknown): string | null => {
  if (typeof value === 'string') {
    return value;
  }
  if (value instanceof Date) {
    return value.toISOString();
  }
  return null;
};

const MAX_BATCH_DOCUMENT_SELECTION = 10;
const DOCUMENTS_PAGE_SIZE_OPTIONS = [10, 20, 50, 100] as const;

const configuredDocumentsPageSize = Number(import.meta.env.VITE_DOCUMENTS_LIBRARY_DEFAULT_PAGE_SIZE ?? 20);
const DEFAULT_DOCUMENTS_PAGE_SIZE = DOCUMENTS_PAGE_SIZE_OPTIONS.includes(
  configuredDocumentsPageSize as (typeof DOCUMENTS_PAGE_SIZE_OPTIONS)[number],
)
  ? configuredDocumentsPageSize
  : 20;
const configuredDocumentsSearchDebounceMs = Number(
  import.meta.env.VITE_DOCUMENTS_LIBRARY_SEARCH_DEBOUNCE_MS ?? 300,
);
const DOCUMENTS_SEARCH_DEBOUNCE_MS = Number.isFinite(configuredDocumentsSearchDebounceMs)
  ? Math.max(0, configuredDocumentsSearchDebounceMs)
  : 300;

const sortFieldForApi = (field: string | undefined): string => {
  switch (field) {
    case 'filename':
    case 'fileSize':
    case 'vectorCount':
      return field;
    case 'creationDate':
    default:
      return 'creationDate';
  }
};

export const buildDocumentListSearchParams = (
  paginationModel: GridPaginationModel,
  sortModel: GridSortModel,
  filters: DocumentFilter,
): URLSearchParams => {
  const params = new URLSearchParams({
    page: String(paginationModel.page + 1),
    page_size: String(paginationModel.pageSize),
    sort_by: sortFieldForApi(sortModel[0]?.field),
    sort_order: sortModel[0]?.sort === 'asc' ? 'asc' : 'desc',
  });
  if (filters.searchTerm) {
    params.set('search', filters.searchTerm);
  }
  filters.embeddingStatus?.forEach((status) => params.append('embedding_status', status));
  if (filters.dateFrom) {
    params.set('date_from', filters.dateFrom.toISOString());
  }
  if (filters.dateTo) {
    params.set('date_to', filters.dateTo.toISOString());
  }
  if (filters.minVectorCount !== undefined) {
    params.set('min_vector_count', String(filters.minVectorCount));
  }
  if (filters.maxVectorCount !== undefined) {
    params.set('max_vector_count', String(filters.maxVectorCount));
  }
  return params;
};

export const lastDocumentPage = (totalDocuments: number, pageSize: number): number => (
  Math.max(0, Math.ceil(totalDocuments / pageSize) - 1)
);

const useDebouncedDocumentSearchTerm = (searchTerm: string): string => {
  const [debouncedSearchTerm, setDebouncedSearchTerm] = useState(searchTerm);

  useEffect(() => {
    const timeoutId = window.setTimeout(
      () => setDebouncedSearchTerm(searchTerm),
      DOCUMENTS_SEARCH_DEBOUNCE_MS,
    );
    return () => window.clearTimeout(timeoutId);
  }, [searchTerm]);

  return debouncedSearchTerm;
};

function DocumentsPageSectionFallback() {
  return (
    <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: 160 }}>
      <Typography variant="body2" color="text.secondary">
        Loading documents UI...
      </Typography>
    </Box>
  );
}

const DocumentsPage: React.FC = () => {
  const navigate = useNavigate();
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [totalCount, setTotalCount] = useState(0);
  const [filters, setFilters] = useState<DocumentFilter>({});
  const [paginationModel, setPaginationModel] = useState<GridPaginationModel>({
    page: 0,
    pageSize: DEFAULT_DOCUMENTS_PAGE_SIZE,
  });
  const [sortModel, setSortModel] = useState<GridSortModel>([
    { field: 'creationDate', sort: 'desc' },
  ]);
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<string[]>([]);
  const [snackbar, setSnackbar] = useState<{
    open: boolean;
    message: string;
    severity: 'success' | 'error' | 'info';
  }>({ open: false, message: '', severity: 'info' });
  const requestControllerRef = useRef<AbortController | null>(null);
  const documentRequestIdRef = useRef(0);
  const debouncedSearchTerm = useDebouncedDocumentSearchTerm(filters.searchTerm ?? '');

  const queryFilters = useMemo(
    () => ({
      searchTerm: debouncedSearchTerm || undefined,
      embeddingStatus: filters.embeddingStatus,
      dateFrom: filters.dateFrom,
      dateTo: filters.dateTo,
      minVectorCount: filters.minVectorCount,
      maxVectorCount: filters.maxVectorCount,
    }),
    [
      debouncedSearchTerm,
      filters.dateFrom,
      filters.dateTo,
      filters.embeddingStatus,
      filters.maxVectorCount,
      filters.minVectorCount,
    ],
  );

  const handleRefresh = React.useCallback(async () => {
    requestControllerRef.current?.abort();
    const requestController = new AbortController();
    requestControllerRef.current = requestController;
    const requestId = documentRequestIdRef.current + 1;
    documentRequestIdRef.current = requestId;
    setLoading(true);
    try {
      console.log('[DocumentsPage] Refresh start');
      const params = buildDocumentListSearchParams(paginationModel, sortModel, queryFilters);

      const response = await fetch(`/api/weaviate/documents?${params.toString()}`, {
        credentials: 'include', // Include httpOnly cookies for authentication
        signal: requestController.signal,
        headers: {
          Accept: 'application/json',
        },
      });

      if (!response.ok) {
        throw new Error(`Failed to load documents (${response.status})`);
      }

      const data = (await response.json()) as DocumentListResponse;
      if (requestId !== documentRequestIdRef.current) {
        return;
      }
      const docs = (data.documents || []) as unknown as Array<Record<string, unknown>>;
      const normalizedDocs: DocumentSummary[] = docs.map((doc) => {
        const processingStatus = readString(
          doc.status ?? doc.processing_status ?? doc.processingStatus,
          'pending',
        ).toLowerCase() as DocumentSummary['processingStatus'];

        return {
          id: readString(doc.document_id ?? doc.id, ''),
          filename: readString(doc.filename, 'Untitled'),
          title: typeof doc.title === 'string' ? doc.title : null,
          fileSize: typeof doc.file_size_bytes === 'number' ? doc.file_size_bytes : (typeof doc.file_size === 'number' ? doc.file_size : (typeof doc.fileSize === 'number' ? doc.fileSize : null)),
          creationDate: readNullableString(doc.upload_timestamp ?? doc.creation_date ?? doc.creationDate),
          lastAccessedDate: readNullableString(doc.last_accessed_date ?? doc.lastAccessedDate),
          processingStatus,
          embeddingStatus: readString(
            doc.embedding_status ?? doc.embeddingStatus,
            'pending',
          ) as DocumentSummary['embeddingStatus'],
          errorMessage: readNullableString(doc.error_message ?? doc.errorMessage),
          chunkCount: typeof doc.chunk_count === 'number' ? doc.chunk_count : (typeof doc.chunkCount === 'number' ? doc.chunkCount : 0),
          vectorCount: typeof doc.vector_count === 'number' ? doc.vector_count : (typeof doc.vectorCount === 'number' ? doc.vectorCount : 0),
          sourceProvenance: normalizeDocumentSourceProvenance(
            doc.source_provenance ?? doc.sourceProvenance,
          ),
        };
      });
      const totalItems =
        data.total ?? data.pagination?.totalItems ?? (data.pagination as Record<string, unknown> | undefined)?.total_items as number ?? docs.length;
      const lastPage = lastDocumentPage(totalItems, paginationModel.pageSize);
      if (paginationModel.page > lastPage) {
        setPaginationModel((previous) => ({ ...previous, page: lastPage }));
        return;
      }
      setDocuments(normalizedDocs);
      setTotalCount(totalItems);
      console.log('[DocumentsPage] Refresh success', { count: normalizedDocs.length });
    } catch (error) {
      if (requestController.signal.aborted || requestId !== documentRequestIdRef.current) {
        return;
      }
      console.error('Error fetching documents:', error);
      // For now, just set empty data to prevent errors
      setDocuments([]);
      setTotalCount(0);
    } finally {
      if (requestId === documentRequestIdRef.current) {
        setLoading(false);
      }
    }
  }, [paginationModel, queryFilters, sortModel]);

  useEffect(() => {
    console.log('[DocumentsPage] Mounted – triggering initial refresh');
    void handleRefresh();
  }, [handleRefresh]);

  useEffect(() => () => requestControllerRef.current?.abort(), []);

  const buildActionErrorMessage = React.useCallback(async (response: Response, fallback: string) => {
    try {
      const payload = await response.json();
      const detail = payload?.detail;
      if (typeof detail === 'string' && detail.trim()) {
        return detail;
      }
      if (detail && typeof detail === 'object' && typeof detail.message === 'string' && detail.message.trim()) {
        return detail.message;
      }
    } catch {
      // Fall through to status-based fallback message.
    }
    return `${fallback} (${response.status})`;
  }, []);

  const handleDelete = React.useCallback(async (id: string) => {
    try {
      const response = await fetch(`/api/weaviate/documents/${id}`, {
        method: 'DELETE',
        credentials: 'include', // Include httpOnly cookies for authentication
      });
      if (!response.ok) {
        const message = await buildActionErrorMessage(response, 'Failed to delete document');
        setSnackbar({ open: true, message, severity: 'error' });
        return;
      }
      void handleRefresh();
    } catch (error) {
      console.error('Error deleting document:', error);
      setSnackbar({
        open: true,
        message: error instanceof Error ? error.message : 'Failed to delete document',
        severity: 'error',
      });
    }
  }, [buildActionErrorMessage, handleRefresh]);

  const handleReembed = React.useCallback(async (id: string) => {
    try {
      const response = await fetch(`/api/weaviate/documents/${id}/reembed`, {
        method: 'POST',
        credentials: 'include', // Include httpOnly cookies for authentication
      });
      if (!response.ok) {
        const message = await buildActionErrorMessage(response, 'Failed to re-embed document');
        setSnackbar({ open: true, message, severity: 'error' });
        return;
      }
      void handleRefresh();
    } catch (error) {
      console.error('Error re-embedding document:', error);
      setSnackbar({
        open: true,
        message: error instanceof Error ? error.message : 'Failed to re-embed document',
        severity: 'error',
      });
    }
  }, [buildActionErrorMessage, handleRefresh]);

  const handleTitleUpdate = React.useCallback(async (documentId: string, title: string) => {
    const response = await fetch(`/api/weaviate/documents/${documentId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ title }),
    });
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || 'Failed to update title');
    }
    const payload = (await response.json()) as { title?: string | null };
    const savedTitle = payload.title ?? null;
    setDocuments((previousDocuments) => previousDocuments.map((document) => (
      document.id === documentId
        ? { ...document, title: savedTitle }
        : document
    )));
    setSnackbar({
      open: true,
      message: savedTitle
        ? 'Display title updated. The original filename was not changed.'
        : 'Display title cleared. The original filename was not changed.',
      severity: 'success',
    });
  }, []);

  const handleFilterChange = React.useCallback((newFilters: DocumentFilter) => {
    setFilters(newFilters);
    setPaginationModel((previous) => ({ ...previous, page: 0 }));
    setSelectedDocumentIds([]);
  }, []);

  const handleClearFilters = React.useCallback(() => {
    setFilters({});
    setPaginationModel((previous) => ({ ...previous, page: 0 }));
    setSelectedDocumentIds([]);
  }, []);

  const handlePaginationModelChange = React.useCallback((nextPaginationModel: GridPaginationModel) => {
    setPaginationModel(nextPaginationModel);
    setSelectedDocumentIds([]);
  }, []);

  const handleSortModelChange = React.useCallback((nextSortModel: GridSortModel) => {
    setSortModel(nextSortModel);
    setPaginationModel((previous) => ({ ...previous, page: 0 }));
    setSelectedDocumentIds([]);
  }, []);

  // Navigate to batch page with selected documents
  const handleStartBatch = React.useCallback(() => {
    // Get full document info for selected IDs
    const selectedDocs = documents.filter(doc => selectedDocumentIds.includes(doc.id));
    navigate('/batch', {
      state: {
        selectedDocumentIds,
        selectedDocuments: selectedDocs.map(doc => ({
          id: doc.id,
          title: doc.title || doc.filename,
        })),
      },
    });
  }, [documents, navigate, selectedDocumentIds]);

  const handleSelectionChange = React.useCallback((ids: string[]) => {
    if (ids.length <= MAX_BATCH_DOCUMENT_SELECTION) {
      setSelectedDocumentIds(ids);
      return;
    }

    setSelectedDocumentIds(ids.slice(0, MAX_BATCH_DOCUMENT_SELECTION));
    setSnackbar({
      open: true,
      message: `You can select up to ${MAX_BATCH_DOCUMENT_SELECTION} documents per batch.`,
      severity: 'info',
    });
  }, []);

  const filterBar = React.useMemo(
    () => (
      <Box sx={{ py: 1, borderBottom: 1, borderColor: 'divider' }}>
        <Suspense fallback={<DocumentsPageSectionFallback />}>
          <InlineFilterBar
            filters={filters}
            onFilterChange={handleFilterChange}
            onClear={handleClearFilters}
          />
        </Suspense>
      </Box>
    ),
    [filters, handleClearFilters, handleFilterChange]
  );

  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        flex: '1 1 auto',
        minHeight: 0,
        height: '100%',
        width: '100%',
        overflow: 'hidden',
      }}
    >
      {/* Selection Bar - shows when documents are selected */}
      {selectedDocumentIds.length > 0 && (
        <Paper
          elevation={2}
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            px: 2,
            py: 1,
            bgcolor: 'primary.main',
            color: 'primary.contrastText',
            borderRadius: 0,
          }}
        >
          <Typography variant="body2">
            {selectedDocumentIds.length} document{selectedDocumentIds.length > 1 ? 's' : ''} selected
            {' '}({MAX_BATCH_DOCUMENT_SELECTION} max)
          </Typography>
          <Box sx={{ display: 'flex', gap: 1 }}>
            <Button
              size="small"
              variant="outlined"
              onClick={() => setSelectedDocumentIds([])}
              sx={{ color: 'inherit', borderColor: 'rgba(255,255,255,0.5)' }}
            >
              Clear Selection
            </Button>
            <Button
              size="small"
              variant="contained"
              startIcon={<BatchIcon />}
              onClick={handleStartBatch}
              sx={{ bgcolor: 'background.paper', color: 'primary.main', '&:hover': { bgcolor: 'grey.100' } }}
            >
              Start Batch
            </Button>
          </Box>
        </Paper>
      )}

      {/* Main Content */}
      <Box
        sx={{
          flex: '1 1 auto',
          minHeight: 0,
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        <Suspense fallback={<DocumentsPageSectionFallback />}>
          <DocumentList
            documents={documents}
            loading={loading}
            totalCount={totalCount}
            onDelete={handleDelete}
            onReembed={handleReembed}
            onRefresh={handleRefresh}
            onTitleUpdate={handleTitleUpdate}
            showUploadControls={false}
            checkboxSelection={true}
            selectedIds={selectedDocumentIds}
            onSelectionChange={handleSelectionChange}
            filterBar={filterBar}
            paginationModel={paginationModel}
            onPaginationModelChange={handlePaginationModelChange}
            sortModel={sortModel}
            onSortModelChange={handleSortModelChange}
          />
        </Suspense>
      </Box>

      <Snackbar
        open={snackbar.open}
        autoHideDuration={4000}
        onClose={() => setSnackbar((prev) => ({ ...prev, open: false }))}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
      >
        <Alert
          severity={snackbar.severity}
          onClose={() => setSnackbar((prev) => ({ ...prev, open: false }))}
          sx={{ width: '100%' }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Box>
  );
};

export default DocumentsPage;
