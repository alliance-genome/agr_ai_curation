import React, { Suspense, lazy, useState, useEffect, useMemo } from 'react';
import { Alert, Box, Button, Paper, Snackbar, Typography } from '@mui/material';
import { PlaylistPlay as BatchIcon } from '@mui/icons-material';
import { useNavigate } from 'react-router-dom';
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
  const [, setTotalCount] = useState(0);
  const [filters, setFilters] = useState<DocumentFilter>({});
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<string[]>([]);
  const [snackbar, setSnackbar] = useState<{
    open: boolean;
    message: string;
    severity: 'success' | 'error' | 'info';
  }>({ open: false, message: '', severity: 'info' });

  const handleRefresh = React.useCallback(async () => {
    setLoading(true);
    try {
      console.log('[DocumentsPage] Refresh start');
      const params = new URLSearchParams({
        page: '1',
        page_size: '100',
      });

      const response = await fetch(`/api/weaviate/documents?${params.toString()}`, {
        credentials: 'include', // Include httpOnly cookies for authentication
        headers: {
          Accept: 'application/json',
        },
      });

      if (!response.ok) {
        throw new Error(`Failed to load documents (${response.status})`);
      }

      const data = (await response.json()) as DocumentListResponse;
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
          chunkCount: typeof doc.chunk_count === 'number' ? doc.chunk_count : (typeof doc.chunkCount === 'number' ? doc.chunkCount : 0),
          vectorCount: typeof doc.vector_count === 'number' ? doc.vector_count : (typeof doc.vectorCount === 'number' ? doc.vectorCount : 0),
          sourceProvenance: normalizeDocumentSourceProvenance(
            doc.source_provenance ?? doc.sourceProvenance,
          ),
        };
      });
      setDocuments(normalizedDocs);
      const totalItems =
        data.pagination?.totalItems ?? (data.pagination as Record<string, unknown> | undefined)?.total_items as number ?? docs.length;
      setTotalCount(totalItems);
      console.log('[DocumentsPage] Refresh success', { count: normalizedDocs.length });
    } catch (error) {
      console.error('Error fetching documents:', error);
      // For now, just set empty data to prevent errors
      setDocuments([]);
      setTotalCount(0);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    console.log('[DocumentsPage] Mounted – triggering initial refresh');
    void handleRefresh();
  }, [handleRefresh]);

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
  }, []);

  // Client-side filtering
  const filteredDocuments = useMemo(() => {
    let result = documents;

    // Filter by search term (filename or title)
    if (filters.searchTerm) {
      const searchLower = filters.searchTerm.toLowerCase();
      result = result.filter(
        (doc) =>
          doc.filename.toLowerCase().includes(searchLower) ||
          (doc.title && doc.title.toLowerCase().includes(searchLower))
      );
    }

    // Filter by embedding status
    if (filters.embeddingStatus && filters.embeddingStatus.length > 0) {
      result = result.filter((doc) =>
        filters.embeddingStatus!.includes(doc.embeddingStatus ?? 'pending')
      );
    }

    // Filter by date range
    if (filters.dateFrom) {
      const fromDate = filters.dateFrom.getTime();
      result = result.filter((doc) => {
        if (!doc.creationDate) return false;
        return new Date(doc.creationDate).getTime() >= fromDate;
      });
    }
    if (filters.dateTo) {
      const toDate = filters.dateTo.getTime();
      result = result.filter((doc) => {
        if (!doc.creationDate) return false;
        return new Date(doc.creationDate).getTime() <= toDate;
      });
    }

    // Filter by chunk count (UI shows "Chunks", filter params still named vectorCount for compatibility)
    if (filters.minVectorCount !== undefined) {
      result = result.filter((doc) => (doc.chunkCount ?? 0) >= filters.minVectorCount!);
    }
    if (filters.maxVectorCount !== undefined) {
      result = result.filter((doc) => (doc.chunkCount ?? 0) <= filters.maxVectorCount!);
    }

    return result;
  }, [documents, filters]);

  const handleFilterChange = React.useCallback((newFilters: DocumentFilter) => {
    setFilters((prev) => ({ ...prev, ...newFilters }));
  }, []);

  const handleClearFilters = React.useCallback(() => {
    setFilters({});
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
            documents={filteredDocuments}
            loading={loading}
            totalCount={filteredDocuments.length}
            onDelete={handleDelete}
            onReembed={handleReembed}
            onRefresh={handleRefresh}
            onTitleUpdate={handleTitleUpdate}
            showUploadControls={false}
            checkboxSelection={true}
            selectedIds={selectedDocumentIds}
            onSelectionChange={handleSelectionChange}
            filterBar={filterBar}
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
