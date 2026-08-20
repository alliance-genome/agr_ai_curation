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
  isActiveDocumentStatus,
  useDocument,
} from '../../services/weaviate';
import {
  documentSourceProviderLabel,
  documentSourceReferenceLabel,
} from '../../utils/documentSourcePresentation';

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
): 'default' | 'primary' | 'success' | 'error' => {
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

  const documentTitle = details?.document.filename ?? 'Document details';
  const processingStatusCurrent = details?.document.processingStatus ?? null;
  const sourceProvenance = details?.document.sourceProvenance ?? null;

  const processingActive = isActiveDocumentStatus(processingStatusCurrent);
  const actionsDisabled = disableActions || actionLoading || isFetching || processingActive;

  const renderInfoItem = (label: string, value: React.ReactNode) => (
    <Box key={label} sx={{ mb: 1.5 }}>
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
      <Typography variant="body1">{value ?? '—'}</Typography>
    </Box>
  );

  const externalIdsText = React.useMemo(() => {
    const externalIds = sourceProvenance?.externalIds;
    if (!externalIds) {
      return null;
    }
    return Object.entries(externalIds)
      .map(([key, value]) => `${key.toUpperCase()}: ${Array.isArray(value) ? value.join(', ') : value}`)
      .join(' · ');
  }, [sourceProvenance]);

  const accessGroupsText = React.useMemo(() => {
    const accessGroupIds = sourceProvenance?.accessGroupIds;
    return accessGroupIds?.length ? accessGroupIds.join(', ') : null;
  }, [sourceProvenance]);

  const sourceReferenceText = documentSourceReferenceLabel(sourceProvenance);

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
                label={`Processing: ${details.document.processingStatus}`}
                color={getStatusColor(details.document.processingStatus)}
                size="small"
              />
              <Chip
                label={`Chunks: ${details.document.chunkCount ?? '—'}`}
                size="small"
                variant="outlined"
              />
            </Stack>

            <Grid container spacing={2}>
              <Grid item xs={12}>
                <Paper variant="outlined" sx={{ p: 2 }}>
                  <Typography variant="subtitle2" gutterBottom>
                    Document Info
                  </Typography>
                  <Divider sx={{ mb: 2 }} />
                  {renderInfoItem('Filename', details.document.filename)}
                  {renderInfoItem('File Size', formatFileSize(details.document.fileSize))}
                  {renderInfoItem('Created', formatDateTime(details.document.creationDate))}
                </Paper>
              </Grid>
              <Grid item xs={12}>
                <Paper variant="outlined" sx={{ p: 2 }}>
                  <Typography variant="subtitle2" gutterBottom>
                    Source
                  </Typography>
                  <Divider sx={{ mb: 2 }} />
                  <Grid container spacing={2}>
                    <Grid item xs={12} md={6}>
                      {renderInfoItem('Provider', documentSourceProviderLabel(sourceProvenance))}
                      {renderInfoItem(
                        'Reference',
                        sourceReferenceText
                      )}
                      {renderInfoItem('External IDs', externalIdsText)}
                      {renderInfoItem('Source MD5', sourceProvenance?.sourceMd5)}
                    </Grid>
                    <Grid item xs={12} md={6}>
                      {renderInfoItem('Source File', sourceProvenance?.sourceFileId)}
                      {renderInfoItem('PDF Artifact', sourceProvenance?.pdfArtifactId)}
                      {renderInfoItem('Converted Artifact', sourceProvenance?.convertedArtifactId)}
                      {renderInfoItem(
                        'Converted File',
                        [sourceProvenance?.fileClass, sourceProvenance?.fileExtension]
                          .filter(Boolean)
                          .join(' / ') || null
                      )}
                      {renderInfoItem('Import Status', sourceProvenance?.importStatus ?? sourceProvenance?.artifactStatus)}
                      {renderInfoItem('Access', sourceProvenance?.accessScope)}
                      {accessGroupsText && renderInfoItem('Access groups', accessGroupsText)}
                      {renderInfoItem('Viewer Mode', sourceProvenance?.viewerMode)}
                    </Grid>
                  </Grid>
                </Paper>
              </Grid>
            </Grid>

            {details.document.errorMessage && (
              <Alert severity="error">{details.document.errorMessage}</Alert>
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
            disabled={actionsDisabled}
          >
            Re-embed
          </Button>
        )}
        {onDelete && (
          <Button
            variant="outlined"
            color="error"
            onClick={handleDelete}
            disabled={actionsDisabled}
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
