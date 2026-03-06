import React, { useState, useEffect, useMemo, useRef } from 'react';
import { Alert, Box, Button, Paper, Snackbar, Typography } from '@mui/material';
import { PlaylistPlay as BatchIcon } from '@mui/icons-material';
import { useNavigate } from 'react-router-dom';
import DocumentList from '../../components/weaviate/DocumentList';
import PdfJobsPanel from '../../components/weaviate/PdfJobsPanel';
import InlineFilterBar from '../../components/weaviate/InlineFilterBar';
import {
  cancelPdfJob,
  DocumentSummary,
  DocumentListResponse,
  DocumentFilter,
  fetchPdfJobs,
  PdfProcessingJob,
} from '../../services/weaviate';
import { emitGlobalToast } from '../../lib/globalNotifications';
import {
  dispatchChatDocumentChanged,
  loadDocumentForChat,
} from '@/features/documents/pdfUploadFlow';
import { buildPdfTerminalNotification } from '@/features/documents/pdfTerminalNotifications';

type PipelineState = {
  busy: boolean;
  message?: string;
};

const MAX_BATCH_DOCUMENT_SELECTION = 10;

const areJobsEquivalent = (previous: PdfProcessingJob[], next: PdfProcessingJob[]): boolean => {
  if (previous === next) {
    return true;
  }
  if (previous.length !== next.length) {
    return false;
  }

  for (let index = 0; index < previous.length; index += 1) {
    const prevJob = previous[index];
    const nextJob = next[index];

    if (
      prevJob.job_id !== nextJob.job_id ||
      prevJob.status !== nextJob.status ||
      prevJob.progress_percentage !== nextJob.progress_percentage ||
      prevJob.current_stage !== nextJob.current_stage ||
      prevJob.message !== nextJob.message ||
      prevJob.error_message !== nextJob.error_message ||
      prevJob.cancel_requested !== nextJob.cancel_requested ||
      prevJob.updated_at !== nextJob.updated_at ||
      prevJob.completed_at !== nextJob.completed_at
    ) {
      return false;
    }
  }

  return true;
};

const DocumentsPage: React.FC = () => {
  const navigate = useNavigate();
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [, setTotalCount] = useState(0);
  const [pipelineState, setPipelineState] = useState<PipelineState>({ busy: false });
  const [filters, setFilters] = useState<DocumentFilter>({});
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<string[]>([]);
  const [jobs, setJobs] = useState<PdfProcessingJob[]>([]);
  const [jobsLoading, setJobsLoading] = useState(false);
  const [snackbar, setSnackbar] = useState<{
    open: boolean;
    message: string;
    severity: 'success' | 'error' | 'info';
  }>({ open: false, message: '', severity: 'info' });
  const [pendingDocumentRefresh, setPendingDocumentRefresh] = useState(false);

  const jobsEventSourceRef = useRef<EventSource | null>(null);
  const jobsPollingRef = useRef<number | null>(null);
  const seenTerminalNotificationsRef = useRef<Set<string>>(new Set());
  const seededTerminalNotificationsRef = useRef(false);

  const notifyTerminalJobTransitions = React.useCallback((nextJobs: PdfProcessingJob[]) => {
    const seedOnly = !seededTerminalNotificationsRef.current;

    for (const job of nextJobs) {
      const notification = buildPdfTerminalNotification(job);
      if (!notification) {
        continue;
      }

      const key = notification.key;
      if (seenTerminalNotificationsRef.current.has(key)) {
        continue;
      }
      seenTerminalNotificationsRef.current.add(key);
      if (seedOnly) {
        continue;
      }

      emitGlobalToast({ message: notification.message, severity: notification.severity });

      setPendingDocumentRefresh(true);
    }

    if (!seededTerminalNotificationsRef.current) {
      seededTerminalNotificationsRef.current = true;
    }
  }, []);

  const applyJobsUpdate = React.useCallback(
    (nextJobs: PdfProcessingJob[]) => {
      setJobs((previousJobs) => (areJobsEquivalent(previousJobs, nextJobs) ? previousJobs : nextJobs));
      notifyTerminalJobTransitions(nextJobs);
    },
    [notifyTerminalJobTransitions]
  );

  const refreshJobs = React.useCallback(async (silent = false) => {
    if (!silent) {
      setJobsLoading(true);
    }

    try {
      const payload = await fetchPdfJobs({ windowDays: 7, limit: 50, offset: 0 });
      applyJobsUpdate(payload.jobs);
    } catch (error) {
      console.error('Error fetching PDF jobs:', error);
    } finally {
      if (!silent) {
        setJobsLoading(false);
      }
    }
  }, [applyJobsUpdate]);

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
      const docs = data.documents || [];
      const normalizedDocs = docs.map((doc: Record<string, unknown>) => ({
          id: doc.document_id ?? doc.id,  // Backend returns document_id, fallback to id for compatibility
          filename: doc.filename ?? 'Untitled',
          title: doc.title ?? null,
          fileSize: typeof doc.file_size_bytes === 'number' ? doc.file_size_bytes : (typeof doc.file_size === 'number' ? doc.file_size : (typeof doc.fileSize === 'number' ? doc.fileSize : null)),
          creationDate: doc.upload_timestamp ?? doc.creation_date ?? doc.creationDate ?? null,
          lastAccessedDate: doc.last_accessed_date ?? doc.lastAccessedDate ?? null,
          processingStatus: ((doc.status ?? doc.processing_status ?? doc.processingStatus ?? 'pending').toLowerCase()) as
            | 'pending'
            | 'parsing'
            | 'chunking'
            | 'embedding'
            | 'storing'
            | 'completed'
            | 'failed',
          embeddingStatus: (doc.embedding_status ?? doc.embeddingStatus ?? 'pending') as
            | 'pending'
            | 'processing'
            | 'completed'
            | 'failed'
            | 'partial',
          chunkCount: typeof doc.chunk_count === 'number' ? doc.chunk_count : (typeof doc.chunkCount === 'number' ? doc.chunkCount : 0),
          vectorCount: typeof doc.vector_count === 'number' ? doc.vector_count : (typeof doc.vectorCount === 'number' ? doc.vectorCount : 0),
        }));
      setDocuments(normalizedDocs);
      const totalItems =
        data.pagination?.totalItems ?? (data.pagination as Record<string, unknown> | undefined)?.total_items as number ?? docs.length;
      setTotalCount(totalItems);
      setPipelineState((prev) => (prev.busy ? prev : { busy: false }));
      await refreshJobs(true);
      console.log('[DocumentsPage] Refresh success', { count: normalizedDocs.length });
    } catch (error) {
      console.error('Error fetching documents:', error);
      // For now, just set empty data to prevent errors
      setDocuments([]);
      setTotalCount(0);
      setPipelineState({ busy: false });
    } finally {
      setLoading(false);
    }
  }, [refreshJobs]);

  useEffect(() => {
    console.log('[DocumentsPage] Mounted – triggering initial refresh');
    void handleRefresh();
  }, [handleRefresh]);

  useEffect(() => {
    if (!pendingDocumentRefresh) {
      return;
    }

    setPendingDocumentRefresh(false);
    void handleRefresh();
  }, [handleRefresh, pendingDocumentRefresh]);

  useEffect(() => {
    const startJobsPolling = () => {
      if (jobsPollingRef.current !== null) {
        return;
      }
      jobsPollingRef.current = window.setInterval(() => {
        void refreshJobs(true);
      }, 5000);
    };

    try {
      const source = new EventSource('/api/weaviate/pdf-jobs/stream?window_days=7&limit=50');
      jobsEventSourceRef.current = source;

      source.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data) as { jobs?: PdfProcessingJob[]; final?: boolean };
          if (payload.final) {
            return;
          }
          if (Array.isArray(payload.jobs)) {
            applyJobsUpdate(payload.jobs);
          }
        } catch (parseError) {
          console.error('Failed to parse PDF jobs stream payload:', parseError);
        }
      };

      source.onerror = () => {
        source.close();
        jobsEventSourceRef.current = null;
        startJobsPolling();
      };
    } catch (error) {
      console.error('Failed to open PDF jobs stream:', error);
      startJobsPolling();
    }

    return () => {
      if (jobsEventSourceRef.current) {
        jobsEventSourceRef.current.close();
        jobsEventSourceRef.current = null;
      }
      if (jobsPollingRef.current !== null) {
        window.clearInterval(jobsPollingRef.current);
        jobsPollingRef.current = null;
      }
    };
  }, [applyJobsUpdate, refreshJobs]);

  const handleCancelJob = React.useCallback(async (jobId: string) => {
    try {
      const response = await cancelPdfJob(jobId);
      setSnackbar({ open: true, message: response.message, severity: 'info' });
      await refreshJobs(true);
    } catch (error) {
      console.error('Failed to cancel PDF job:', error);
      setSnackbar({
        open: true,
        message: error instanceof Error ? error.message : 'Failed to cancel job',
        severity: 'error',
      });
    }
  }, [refreshJobs]);

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
    } catch (_error) {
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

  const handleLoad = React.useCallback(async (summary: DocumentSummary) => {
    try {
      console.log('[DocumentsPage] Loading document for chat:', summary.id, summary.filename);
      const payload = await loadDocumentForChat(summary.id);
      console.log('[DocumentsPage] Document saved to backend:', payload);

      // Dispatch chat-document-changed event so Chat component updates immediately
      // This triggers Chat's event listener which will call fetchActiveDocument()
      // and load the PDF in the viewer
      console.log('[DocumentsPage] Dispatching chat-document-changed event with payload:', payload);
      dispatchChatDocumentChanged(payload);

    } catch (error) {
      console.error('[DocumentsPage] Error loading document:', error);
      window.alert(error instanceof Error ? error.message : 'Failed to load document for chat');
    }
  }, []);

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

  const handlePipelineStateChange = React.useCallback((busy: boolean, message?: string) => {
    setPipelineState({ busy, message });
  }, []);

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
        <InlineFilterBar
          filters={filters}
          onFilterChange={handleFilterChange}
          onClear={handleClearFilters}
        />
      </Box>
    ),
    [filters, handleClearFilters, handleFilterChange]
  );

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
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
      <Box sx={{ flexGrow: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
        <PdfJobsPanel jobs={jobs} loading={jobsLoading} onCancelJob={handleCancelJob} />
        <DocumentList
          documents={filteredDocuments}
          loading={loading}
          totalCount={filteredDocuments.length}
          onDelete={handleDelete}
          onReembed={handleReembed}
          onRefresh={handleRefresh}
          onLoad={handleLoad}
          onTitleUpdate={handleTitleUpdate}
          pipelineBusy={pipelineState.busy}
          pipelineMessage={pipelineState.message}
          onPipelineStateChange={handlePipelineStateChange}
          checkboxSelection={true}
          selectedIds={selectedDocumentIds}
          onSelectionChange={handleSelectionChange}
          filterBar={filterBar}
        />
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
