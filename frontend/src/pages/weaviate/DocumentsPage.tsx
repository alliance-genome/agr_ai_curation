import React, { useState, useEffect, useMemo } from 'react';
import { Box, Button, Paper, Typography } from '@mui/material';
import { PlaylistPlay as BatchIcon } from '@mui/icons-material';
import { useNavigate } from 'react-router-dom';
import DocumentList from '../../components/weaviate/DocumentList';
import InlineFilterBar from '../../components/weaviate/InlineFilterBar';
import {
  DocumentSummary,
  DocumentListResponse,
  DocumentFilter,
} from '../../services/weaviate';

type PipelineState = {
  busy: boolean;
  message?: string;
};

const DocumentsPage: React.FC = () => {
  const navigate = useNavigate();
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [_totalCount, setTotalCount] = useState(0);
  const [pipelineState, setPipelineState] = useState<PipelineState>({ busy: false });
  const [filters, setFilters] = useState<DocumentFilter>({});
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<string[]>([]);

  const handleRefresh = async () => {
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
      const normalizedDocs = docs.map((doc: any) => ({
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
  };

  useEffect(() => {
    console.log('[DocumentsPage] Mounted – triggering initial refresh');
    handleRefresh();
  }, []);

  const handleDelete = async (id: string) => {
    try {
      const response = await fetch(`/api/weaviate/documents/${id}`, {
        method: 'DELETE',
        credentials: 'include', // Include httpOnly cookies for authentication
      });
      if (response.ok) {
        handleRefresh();
      }
    } catch (error) {
      console.error('Error deleting document:', error);
    }
  };

  const handleReembed = async (id: string) => {
    try {
      const response = await fetch(`/api/weaviate/documents/${id}/reembed`, {
        method: 'POST',
        credentials: 'include', // Include httpOnly cookies for authentication
      });
      if (response.ok) {
        handleRefresh();
      }
    } catch (error) {
      console.error('Error re-embedding document:', error);
    }
  };

  const handleLoad = async (summary: DocumentSummary) => {
    try {
      console.log('[DocumentsPage] Loading document for chat:', summary.id, summary.filename);

      // Save document to backend - this is the source of truth
      const response = await fetch('/api/chat/document/load', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ document_id: summary.id }),
      });

      const payload = await response.json().catch((err) => {
        console.error('[DocumentsPage] Failed to parse response:', err);
        return {};
      });

      if (!response.ok) {
        const detail = payload?.detail ?? 'Failed to load document for chat';
        console.error('[DocumentsPage] Load failed:', detail);
        window.alert(detail);
        return;
      }

      console.log('[DocumentsPage] Document saved to backend:', payload);

      // Dispatch chat-document-changed event so Chat component updates immediately
      // This triggers Chat's event listener which will call fetchActiveDocument()
      // and load the PDF in the viewer
      console.log('[DocumentsPage] Dispatching chat-document-changed event with payload:', payload);
      window.dispatchEvent(new CustomEvent('chat-document-changed', { detail: payload }));

    } catch (error) {
      console.error('[DocumentsPage] Error loading document:', error);
      window.alert('Failed to load document for chat');
    }
  };

  const handleTitleUpdate = async (documentId: string, title: string) => {
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
  };

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

  const handleFilterChange = (newFilters: DocumentFilter) => {
    setFilters((prev) => ({ ...prev, ...newFilters }));
  };

  const handleClearFilters = () => {
    setFilters({});
  };

  // Navigate to batch page with selected documents
  const handleStartBatch = () => {
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
  };

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
      <Box sx={{ flexGrow: 1, minHeight: 0 }}>
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
          onPipelineStateChange={(busy, message) => setPipelineState({ busy, message })}
          checkboxSelection={true}
          selectedIds={selectedDocumentIds}
          onSelectionChange={setSelectedDocumentIds}
          filterBar={
            <Box sx={{ py: 1, borderBottom: 1, borderColor: 'divider' }}>
              <InlineFilterBar
                filters={filters}
                onFilterChange={handleFilterChange}
                onClear={handleClearFilters}
              />
            </Box>
          }
        />
      </Box>
    </Box>
  );
};

export default DocumentsPage;
