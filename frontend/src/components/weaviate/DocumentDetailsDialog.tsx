import React from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  Grid,
  IconButton,
  LinearProgress,
  List,
  ListItem,
  ListItemText,
  Paper,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material';
import {
  Close,
  Refresh as RefreshIcon,
} from '@mui/icons-material';
import {
  DocumentDetailData,
  DocumentSummary,
  EmbeddingModelBreakdown,
  ChunkPreviewSummary,
  useDocument,
} from '../../services/weaviate';

interface DocumentDetailsDialogProps {
  open: boolean;
  documentId: string | null;
  documentSummary?: DocumentSummary;
  onClose: () => void;
  onDelete?: (id: string) => Promise<void> | void;
  onReembed?: (id: string) => Promise<void> | void;
  onRefreshRequested?: () => Promise<void> | void;
  disableActions?: boolean;
}

const formatFileSize = (bytes?: number | null): string => {
  if (bytes === undefined || bytes === null) {
    return '—';
  }
  if (!Number.isFinite(bytes) || bytes < 0) {
    return '—';
  }
  if (bytes === 0) {
    return '0 Bytes';
  }
  const k = 1024;
  const sizeNames = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  const value = bytes / Math.pow(k, i);
  return `${value.toFixed(value >= 100 ? 0 : value >= 10 ? 1 : 2)} ${sizeNames[i]}`;
};

const formatDateTime = (value?: string | Date | null): string => {
  if (!value) {
    return '—';
  }
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value instanceof Date ? value.toISOString() : String(value);
  }
  return date.toLocaleString();
};

const getStatusColor = (
  status: string | null | undefined
): 'default' | 'primary' | 'success' | 'error' | 'warning' => {
  switch (status) {
    case 'completed':
      return 'success';
    case 'failed':
      return 'error';
    case 'processing':
    case 'parsing':
    case 'chunking':
    case 'embedding':
    case 'storing':
      return 'primary';
    case 'partial':
      return 'warning';
    default:
      return 'default';
  }
};

const DocumentDetailsDialog: React.FC<DocumentDetailsDialogProps> = ({
  open,
  documentId,
  documentSummary,
  onClose,
  onDelete,
  onReembed,
  onRefreshRequested,
  disableActions = false,
}) => {
  const [actionError, setActionError] = React.useState<string | null>(null);
  const [actionLoading, setActionLoading] = React.useState(false);

  const placeholderData = React.useMemo<DocumentDetailData | undefined>(() => {
    if (!documentSummary) {
      return undefined;
    }
    return {
      document: documentSummary,
      embeddingSummary: undefined,
      pipelineStatus: undefined,
      chunksPreview: [],
      totalChunks: documentSummary.chunkCount ?? 0,
      relatedDocuments: [],
      schemaVersion: undefined,
    };
  }, [documentSummary]);

  const {
    data,
    isLoading,
    isFetching,
    error,
    refetch,
  } = useDocument(documentId ?? '', {
    enabled: open && !!documentId,
    placeholderData,
    refetchOnWindowFocus: false,
  });

  const details = data ?? placeholderData ?? null;
  const fetchErrorMessage = error instanceof Error ? error.message : null;
  const isInitialLoading = !details && (isLoading || isFetching);

  const handleManualRefresh = React.useCallback(() => {
    if (!documentId) {
      return;
    }
    refetch();
  }, [documentId, refetch]);

  const handleReembed = React.useCallback(async () => {
    if (!documentId || !onReembed) {
      return;
    }
    setActionError(null);
    setActionLoading(true);
    try {
      await onReembed(documentId);
      await refetch({ throwOnError: true });
      if (onRefreshRequested) {
        await onRefreshRequested();
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to re-embed document';
      setActionError(message);
    } finally {
      setActionLoading(false);
    }
  }, [documentId, onReembed, onRefreshRequested, refetch]);

  const handleDelete = React.useCallback(async () => {
    if (!documentId || !onDelete) {
      return;
    }
    setActionError(null);
    setActionLoading(true);
    try {
      await onDelete(documentId);
      if (onRefreshRequested) {
        await onRefreshRequested();
      }
      onClose();
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to delete document';
      setActionError(message);
    } finally {
      setActionLoading(false);
    }
  }, [documentId, onDelete, onRefreshRequested, onClose]);

  const documentTitle = details?.document.filename ?? documentSummary?.filename ?? 'Document details';
  const embeddingSummary = details?.embeddingSummary;
  const pipelineStatus = details?.pipelineStatus;
  const embeddingStatusCurrent = details?.document.embeddingStatus ?? documentSummary?.embeddingStatus ?? null;
  const processingStatusCurrent = details?.document.processingStatus ?? documentSummary?.processingStatus ?? null;

  const disableReembed = disableActions || actionLoading || isFetching || embeddingStatusCurrent === 'processing';
  const disableDelete = disableActions || actionLoading || isFetching || processingStatusCurrent === 'processing';

  const renderInfoItem = (label: string, value: React.ReactNode) => (
    <Box key={label} sx={{ mb: 1.5 }}>
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
      <Typography variant="body1">{value ?? '—'}</Typography>
    </Box>
  );

  const chunkPreview = details?.chunksPreview ?? ([] as ChunkPreviewSummary[]);
  const previewChunks = chunkPreview.slice(0, 3);
  const rawMetadata = details?.document.metadata;
  const metadata = React.useMemo(() => {
    if (rawMetadata && typeof rawMetadata === 'object') {
      return rawMetadata as Record<string, unknown>;
    }
    return undefined;
  }, [rawMetadata]);

  const getMetadataValue = React.useCallback(
    (key: string): string | undefined => {
      if (!metadata) {
        return undefined;
      }
      const value = metadata[key];
      if (value === undefined || value === null) {
        return undefined;
      }
      return String(value);
    },
    [metadata]
  );

  const metadataPageCount = getMetadataValue('page_count');
  const metadataDocumentType = getMetadataValue('document_type');
  const metadataAuthor = getMetadataValue('author');
  const metadataTitle = getMetadataValue('title');
  const metadataLastProcessedStage = getMetadataValue('last_processed_stage');

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle sx={{ pb: 1 }}>
        <Box display="flex" alignItems="center" justifyContent="space-between">
          <Typography variant="h6" component="span">
            {documentTitle}
          </Typography>
          <Stack direction="row" spacing={1} alignItems="center">
            <Tooltip title="Refresh details">
              <span>
                <IconButton
                  onClick={handleManualRefresh}
                  size="small"
                  disabled={!documentId || isFetching}
                  aria-label="refresh details"
                >
                  <RefreshIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
            <IconButton onClick={onClose} size="small" aria-label="close details dialog">
              <Close fontSize="small" />
            </IconButton>
          </Stack>
        </Box>
      </DialogTitle>
      <DialogContent dividers sx={{ pt: 1, pb: 0 }}>
        {isInitialLoading && (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
            <CircularProgress size={28} thickness={4} />
          </Box>
        )}

        {fetchErrorMessage && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {fetchErrorMessage}
          </Alert>
        )}

        {details && (
          <Stack spacing={3} sx={{ pt: 1 }}>
            <Stack direction="row" spacing={2} flexWrap="wrap" useFlexGap>
              <Chip
                label={`Processing: ${details.document.processingStatus ?? 'unknown'}`}
                color={getStatusColor(details.document.processingStatus)}
                size="small"
              />
              <Chip
                label={`Embedding: ${details.document.embeddingStatus ?? 'unknown'}`}
                color={getStatusColor(details.document.embeddingStatus)}
                size="small"
              />
              <Chip
                label={`Vectors: ${details.document.vectorCount ?? 0}`}
                size="small"
                variant="outlined"
              />
              <Chip
                label={`Chunks: ${details.document.chunkCount ?? details.totalChunks ?? 0}`}
                size="small"
                variant="outlined"
              />
              {details.schemaVersion && (
                <Chip label={`Schema v${details.schemaVersion}`} size="small" variant="outlined" />
              )}
            </Stack>

            <Grid container spacing={2}>
              <Grid item xs={12} md={6}>
                <Paper variant="outlined" sx={{ p: 2 }}>
                  <Typography variant="subtitle2" gutterBottom>
                    Document Info
                  </Typography>
                  <Divider sx={{ mb: 2 }} />
                  {renderInfoItem('Filename', details.document.filename)}
                  {renderInfoItem('File Size', formatFileSize(details.document.fileSize))}
                  {renderInfoItem('Created', formatDateTime(details.document.creationDate))}
                  {renderInfoItem('Last Accessed', formatDateTime(details.document.lastAccessedDate))}
                  {renderInfoItem('Page Count', metadataPageCount ?? '—')}
                  {renderInfoItem('Document Type', metadataDocumentType ?? '—')}
                </Paper>
              </Grid>
              <Grid item xs={12} md={6}>
                <Paper variant="outlined" sx={{ p: 2 }}>
                  <Typography variant="subtitle2" gutterBottom>
                    Processing & Embeddings
                  </Typography>
                  <Divider sx={{ mb: 2 }} />
                  {embeddingSummary && (
                    <Box sx={{ mb: 1.5 }}>
                      <Typography variant="caption" color="text.secondary">
                        Embedding Coverage
                      </Typography>
                      <Typography variant="body1">
                        {embeddingSummary.coveragePercentage !== undefined && embeddingSummary.coveragePercentage !== null
                          ? `${embeddingSummary.coveragePercentage.toFixed(2)}%`
                          : `${embeddingSummary.embeddedChunks ?? 0}/${embeddingSummary.totalChunks ?? 0}`}
                      </Typography>
                    </Box>
                  )}
                  {renderInfoItem('Last Embedded', formatDateTime(embeddingSummary?.lastEmbeddedAt ?? null))}
                  {renderInfoItem('Embedding Model', embeddingSummary?.primaryModel ?? (embeddingSummary?.models?.[0]?.name ?? '—'))}
                  {embeddingSummary && embeddingSummary.models.length > 1 && (
                    <Box sx={{ mb: 1.5 }}>
                      <Typography variant="caption" color="text.secondary">
                        Model Breakdown
                      </Typography>
                      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                        {embeddingSummary.models.map((model: EmbeddingModelBreakdown) => (
                          <Chip
                            key={model.name}
                            label={`${model.name}: ${model.chunkCount}`}
                            size="small"
                            variant="outlined"
                          />
                        ))}
                      </Stack>
                    </Box>
                  )}
                  {pipelineStatus && (
                    <Box sx={{ mt: 1.5 }}>
                      <Typography variant="caption" color="text.secondary">
                        Pipeline Stage
                      </Typography>
                      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
                        <Chip
                          label={pipelineStatus.currentStage ?? 'unknown'}
                          size="small"
                          color={getStatusColor(pipelineStatus.currentStage)}
                          variant="outlined"
                        />
                        {typeof pipelineStatus.progressPercentage === 'number' && (
                          <Typography variant="body2" color="text.secondary">
                            {pipelineStatus.progressPercentage}%
                          </Typography>
                        )}
                      </Stack>
                      <LinearProgress
                        variant={typeof pipelineStatus.progressPercentage === 'number' ? 'determinate' : 'indeterminate'}
                        value={pipelineStatus.progressPercentage ?? undefined}
                        sx={{ height: 6, borderRadius: 999, mb: 1 }}
                      />
                      {pipelineStatus.message && (
                        <Typography variant="body2" color="text.secondary">
                          {pipelineStatus.message}
                        </Typography>
                      )}
                      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
                        Updated: {formatDateTime(pipelineStatus.updatedAt)}
                      </Typography>
                    </Box>
                  )}
                </Paper>
              </Grid>
            </Grid>

            {(metadataAuthor || metadataTitle || metadataLastProcessedStage) && (
              <Paper variant="outlined" sx={{ p: 2 }}>
                <Typography variant="subtitle2" gutterBottom>
                  Metadata
                </Typography>
                <Divider sx={{ mb: 2 }} />
                <List dense disablePadding>
                  {metadataAuthor && (
                    <ListItem>
                      <ListItemText primary="Author" secondary={metadataAuthor} />
                    </ListItem>
                  )}
                  {metadataTitle && (
                    <ListItem>
                      <ListItemText primary="Title" secondary={metadataTitle} />
                    </ListItem>
                  )}
                  {metadataLastProcessedStage && (
                    <ListItem>
                      <ListItemText
                        primary="Last Processed Stage"
                        secondary={metadataLastProcessedStage}
                      />
                    </ListItem>
                  )}
                </List>
              </Paper>
            )}

            <Paper variant="outlined" sx={{ p: 2 }}>
              <Typography variant="subtitle2" gutterBottom>
                Chunk Preview
              </Typography>
              <Divider sx={{ mb: 2 }} />
              {previewChunks.length === 0 ? (
                <Typography variant="body2" color="text.secondary">
                  No chunks available for preview.
                </Typography>
              ) : (
                <Stack spacing={2}>
                  {previewChunks.map((chunk: ChunkPreviewSummary) => (
                    <Box key={chunk.id}>
                      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
                        <Typography variant="subtitle2">
                          Chunk #{chunk.chunkIndex ?? '—'}
                        </Typography>
                        {chunk.sectionTitle && (
                          <Typography variant="body2" color="text.secondary">
                            • {chunk.sectionTitle}
                          </Typography>
                        )}
                        {chunk.elementType && (
                          <Chip label={chunk.elementType} size="small" variant="outlined" />
                        )}
                        {chunk.pageNumber && (
                          <Chip label={`Page ${chunk.pageNumber}`} size="small" variant="outlined" />
                        )}
                      </Stack>
                      <Typography
                        variant="body2"
                        sx={{
                          display: '-webkit-box',
                          WebkitLineClamp: 3,
                          WebkitBoxOrient: 'vertical',
                          overflow: 'hidden',
                          whiteSpace: 'pre-wrap',
                        }}
                      >
                        {chunk.content.trim() || '—'}
                      </Typography>
                      {chunk.embeddingModel && (
                        <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5 }}>
                          Embedding model: {chunk.embeddingModel}
                        </Typography>
                      )}
                    </Box>
                  ))}
                  {chunkPreview.length > previewChunks.length && (
                    <Typography variant="caption" color="text.secondary">
                      Showing first {previewChunks.length} of {chunkPreview.length} preview chunks.
                    </Typography>
                  )}
                </Stack>
              )}
            </Paper>

            {details.relatedDocuments.length > 0 && (
              <Paper variant="outlined" sx={{ p: 2 }}>
                <Typography variant="subtitle2" gutterBottom>
                  Related Documents
                </Typography>
                <Divider sx={{ mb: 2 }} />
                <List dense disablePadding>
                  {details.relatedDocuments.map((doc) => (
                    <ListItem key={doc.id} disableGutters>
                      <ListItemText
                        primary={doc.filename}
                        secondary={`Vectors: ${doc.vectorCount ?? 0} • Chunks: ${doc.chunkCount ?? 0}`}
                      />
                    </ListItem>
                  ))}
                </List>
              </Paper>
            )}
          </Stack>
        )}

        {!isInitialLoading && !details && !fetchErrorMessage && (
          <Typography variant="body2" color="text.secondary" sx={{ py: 2 }}>
            No details available for this document.
          </Typography>
        )}
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        {actionError && (
          <Alert severity="error" sx={{ mr: 'auto' }}>
            {actionError}
          </Alert>
        )}
        {onReembed && (
          <Button
            variant="outlined"
            onClick={handleReembed}
            disabled={disableReembed}
          >
            Re-embed
          </Button>
        )}
        {onDelete && (
          <Button
            variant="outlined"
            color="error"
            onClick={handleDelete}
            disabled={disableDelete}
          >
            Delete
          </Button>
        )}
        <Button onClick={onClose} disabled={actionLoading}>
          Close
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default DocumentDetailsDialog;
