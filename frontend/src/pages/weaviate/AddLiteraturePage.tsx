import React from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Chip,
  Divider,
  IconButton,
  LinearProgress,
  Paper,
  Stack,
  Tab,
  Tabs,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
  alpha,
  useTheme,
} from '@mui/material';
import {
  Article as ArticleIcon,
  CheckCircle as CheckCircleIcon,
  CloudUpload as CloudUploadIcon,
  ContentCopy as ContentCopyIcon,
  ErrorOutline as ErrorOutlineIcon,
  FileOpen as FileOpenIcon,
  HourglassTop as HourglassTopIcon,
  PlayArrow as PlayArrowIcon,
  Refresh as RefreshIcon,
  Search as SearchIcon,
  WarningAmber as WarningAmberIcon,
} from '@mui/icons-material';
import {
  MAX_UPLOAD_FILES_PER_SELECTION,
  uploadPdfDocument,
  validatePdfSelection,
} from '@/features/documents/pdfUploadFlow';
import { buildPdfTerminalNotification } from '@/features/documents/pdfTerminalNotifications';
import { emitGlobalToast } from '@/lib/globalNotifications';
import PdfJobsPanel from '../../components/weaviate/PdfJobsPanel';
import {
  cancelPdfJob,
  fetchPdfJobs,
  type PdfProcessingJob,
} from '../../services/weaviate';

type ImportStatus =
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

type ImportMode = 'identifiers' | 'upload';
type PendingAction = 'resolve' | 'import' | null;
type UploadStatus = 'idle' | 'uploading' | 'complete' | 'error';

interface LiteratureImportResult {
  identifier: string;
  normalizedIdentifier: string | null;
  status: ImportStatus;
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
    chunks?: number;
  };
}

interface IdentifierImportApiResult {
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

interface IdentifierImportApiResponse {
  results?: IdentifierImportApiResult[];
  imported_count?: number;
}

const statusTone: Record<
  ImportStatus,
  {
    label: string;
    chipColor: 'success' | 'info' | 'warning' | 'error' | 'default';
    icon: React.ReactElement;
  }
> = {
  resolved: {
    label: 'Resolved',
    chipColor: 'success',
    icon: <CheckCircleIcon fontSize="small" />,
  },
  imported: {
    label: 'Imported',
    chipColor: 'success',
    icon: <CheckCircleIcon fontSize="small" />,
  },
  duplicate: {
    label: 'Duplicate',
    chipColor: 'info',
    icon: <ContentCopyIcon fontSize="small" />,
  },
  invalid: {
    label: 'Invalid',
    chipColor: 'error',
    icon: <ErrorOutlineIcon fontSize="small" />,
  },
  access_denied: {
    label: 'Access denied',
    chipColor: 'warning',
    icon: <WarningAmberIcon fontSize="small" />,
  },
  conversion_running: {
    label: 'Conversion running',
    chipColor: 'warning',
    icon: <HourglassTopIcon fontSize="small" />,
  },
  conversion_failed: {
    label: 'Conversion failed',
    chipColor: 'error',
    icon: <ErrorOutlineIcon fontSize="small" />,
  },
  needs_selection: {
    label: 'Needs selection',
    chipColor: 'warning',
    icon: <WarningAmberIcon fontSize="small" />,
  },
  no_source_pdf: {
    label: 'No source PDF',
    chipColor: 'error',
    icon: <ErrorOutlineIcon fontSize="small" />,
  },
  no_converted_text: {
    label: 'No converted text',
    chipColor: 'warning',
    icon: <WarningAmberIcon fontSize="small" />,
  },
  provider_unavailable: {
    label: 'Provider unavailable',
    chipColor: 'error',
    icon: <ErrorOutlineIcon fontSize="small" />,
  },
};

const postIdentifierBatch = async (
  endpoint: string,
  rawIdentifiers: string,
): Promise<IdentifierImportApiResponse> => {
  const response = await fetch(endpoint, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ identifiers: rawIdentifiers }),
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof payload === 'object' && payload && 'detail' in payload
      ? String((payload as { detail?: unknown }).detail ?? '')
      : '';
    throw new Error(detail || 'Failed to process source identifiers.');
  }

  return payload as IdentifierImportApiResponse;
};

const resolveSourceIdentifiers = (rawIdentifiers: string): Promise<IdentifierImportApiResponse> => (
  postIdentifierBatch('/api/weaviate/documents/resolve/source-identifiers', rawIdentifiers)
);

const importSourceIdentifiers = (rawIdentifiers: string): Promise<IdentifierImportApiResponse> => (
  postIdentifierBatch('/api/weaviate/documents/import/source-identifiers', rawIdentifiers)
);

const statusFromApiResult = (result: IdentifierImportApiResult): ImportStatus => {
  const errorCode = result.error_code ?? '';
  if (result.status === 'resolved') {
    return 'resolved';
  }
  if (result.status === 'imported') {
    return 'imported';
  }
  if (result.status === 'duplicate') {
    return 'duplicate';
  }
  if (errorCode === 'access_denied' || errorCode === 'document_source_access_denied') {
    return 'access_denied';
  }
  if (
    errorCode === 'provider_unavailable' ||
    errorCode === 'document_source_unavailable' ||
    errorCode === 'document_source_curator_token_unavailable'
  ) {
    return 'provider_unavailable';
  }
  if (errorCode === 'conversion_running' || errorCode === 'document_source_conversion_running') {
    return 'conversion_running';
  }
  if (errorCode === 'conversion_failed' || errorCode === 'document_source_conversion_failed') {
    return 'conversion_failed';
  }
  if (errorCode === 'ambiguous_match' || errorCode === 'document_source_ambiguous_match') {
    return 'needs_selection';
  }
  if (errorCode === 'no_source_artifact' || errorCode === 'document_source_no_source_artifact') {
    return 'no_source_pdf';
  }
  if (errorCode === 'no_converted_text' || errorCode === 'document_source_no_converted_text') {
    return 'no_converted_text';
  }
  return 'invalid';
};

const stringFromRecord = (record: Record<string, unknown>, key: string): string | undefined => {
  const value = record[key];
  return typeof value === 'string' && value.trim() ? value : undefined;
};

const numberFromRecord = (record: Record<string, unknown>, key: string): number | undefined => {
  const value = record[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
};

const resultFromApiResult = (result: IdentifierImportApiResult): LiteratureImportResult => {
  const provenance = result.source_provenance ?? undefined;
  const viewerMode = provenance ? stringFromRecord(provenance, 'viewer_mode') : undefined;
  const pdfArtifactId = provenance
    ? stringFromRecord(provenance, 'pdf_artifact_id') ?? stringFromRecord(provenance, 'pdf_referencefile_id')
    : undefined;

  return {
    identifier: result.identifier,
    normalizedIdentifier: result.normalized_identifier ?? null,
    status: statusFromApiResult(result),
    message: result.message || 'Source identifier returned without a message.',
    documentId: result.document_id ?? result.existing_document_id ?? undefined,
    filename: result.filename ?? undefined,
    jobId: result.job_id ?? undefined,
    source: viewerMode === 'local_pdf' && pdfArtifactId
      ? {
          provider: stringFromRecord(provenance!, 'provider') ?? 'document_source',
          viewerMode: 'local_pdf',
          pdfArtifactId,
          convertedArtifactId: stringFromRecord(provenance!, 'converted_artifact_id')
            ?? stringFromRecord(provenance!, 'converted_referencefile_id'),
          sourceMd5: stringFromRecord(provenance!, 'source_md5') ?? 'unknown',
          chunks: numberFromRecord(provenance!, 'chunks') ?? numberFromRecord(provenance!, 'chunk_count'),
        }
      : undefined,
  };
};

const canOpenInDocuments = (result: LiteratureImportResult) => (
  Boolean(result.documentId) && result.status !== 'conversion_running'
);

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

const convertedSourceLabel = (result: LiteratureImportResult): string => {
  if (!result.source) {
    return '';
  }
  if (result.source.convertedArtifactId) {
    return `MD ${result.source.convertedArtifactId}`;
  }
  return result.source.provider === 'manual_upload' ? 'MD pending' : 'MD not available';
};

const AddLiteraturePage: React.FC = () => {
  const theme = useTheme();
  const navigate = useNavigate();
  const [mode, setMode] = React.useState<ImportMode>('identifiers');
  const [identifiers, setIdentifiers] = React.useState('');
  const [results, setResults] = React.useState<LiteratureImportResult[]>([]);
  const [pendingAction, setPendingAction] = React.useState<PendingAction>(null);
  const [uploadStatus, setUploadStatus] = React.useState<UploadStatus>('idle');
  const [uploadMessage, setUploadMessage] = React.useState<string | null>(null);
  const [jobs, setJobs] = React.useState<PdfProcessingJob[]>([]);
  const [jobsLoading, setJobsLoading] = React.useState(false);
  const identifierRequestVersionRef = React.useRef(0);
  const jobsEventSourceRef = React.useRef<EventSource | null>(null);
  const jobsPollingRef = React.useRef<number | null>(null);
  const seenTerminalNotificationsRef = React.useRef<Set<string>>(new Set());
  const seededTerminalNotificationsRef = React.useRef(false);
  const isWorking = pendingAction !== null;
  const isUploading = uploadStatus === 'uploading';

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
    [notifyTerminalJobTransitions],
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

  React.useEffect(() => {
    const startJobsPolling = () => {
      if (jobsPollingRef.current !== null) {
        return;
      }
      jobsPollingRef.current = window.setInterval(() => {
        void refreshJobs(true);
      }, 5000);
    };

    void refreshJobs();

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
      emitGlobalToast({ message: response.message, severity: 'info' });
      await refreshJobs(true);
    } catch (error) {
      console.error('Failed to cancel PDF job:', error);
      emitGlobalToast({
        message: error instanceof Error ? error.message : 'Failed to cancel job',
        severity: 'error',
      });
    }
  }, [refreshJobs]);

  const handleResolve = React.useCallback(() => {
    const requestVersion = identifierRequestVersionRef.current + 1;
    identifierRequestVersionRef.current = requestVersion;
    setPendingAction('resolve');
    resolveSourceIdentifiers(identifiers)
      .then((payload) => {
        if (identifierRequestVersionRef.current !== requestVersion) {
          return;
        }
        setResults(payload.results?.map(resultFromApiResult) ?? []);
      })
      .catch((error) => {
        if (identifierRequestVersionRef.current !== requestVersion) {
          return;
        }
        setResults([{
          identifier: identifiers,
          normalizedIdentifier: null,
          status: 'provider_unavailable',
          message: error instanceof Error ? error.message : 'Failed to resolve source identifiers.',
        }]);
      })
      .finally(() => {
        if (identifierRequestVersionRef.current !== requestVersion) {
          return;
        }
        setPendingAction(null);
      });
  }, [identifiers]);

  const handleResolveAndImport = React.useCallback(() => {
    const requestVersion = identifierRequestVersionRef.current + 1;
    identifierRequestVersionRef.current = requestVersion;
    setPendingAction('import');
    importSourceIdentifiers(identifiers)
      .then((payload) => {
        if (identifierRequestVersionRef.current !== requestVersion) {
          return;
        }
        setResults(payload.results?.map(resultFromApiResult) ?? []);
        if ((payload.imported_count ?? 0) > 0) {
          emitGlobalToast({
            message: 'Identifier imports are processing in the background. You can safely navigate away.',
            severity: 'info',
            autoHideDurationMs: 6000,
            anchorOrigin: { vertical: 'bottom', horizontal: 'left' },
          });
          void refreshJobs(true);
        }
      })
      .catch((error) => {
        if (identifierRequestVersionRef.current !== requestVersion) {
          return;
        }
        setResults([{
          identifier: identifiers,
          normalizedIdentifier: null,
          status: 'provider_unavailable',
          message: error instanceof Error ? error.message : 'Failed to import source identifiers.',
        }]);
      })
      .finally(() => {
        if (identifierRequestVersionRef.current !== requestVersion) {
          return;
        }
        setPendingAction(null);
      });
  }, [identifiers, refreshJobs]);

  const handleReset = React.useCallback(() => {
    setIdentifiers('');
    setResults([]);
    setPendingAction(null);
    identifierRequestVersionRef.current += 1;
    setUploadStatus('idle');
    setUploadMessage(null);
  }, []);

  const handleModeChange = React.useCallback((_event: React.SyntheticEvent, nextMode: ImportMode) => {
    setMode(nextMode);
    identifierRequestVersionRef.current += 1;
    setPendingAction(null);
  }, []);

  const handleViewDocument = React.useCallback(
    (result: LiteratureImportResult) => {
      if (!result.documentId) {
        return;
      }
      navigate('/weaviate/documents');
    },
    [navigate],
  );

  const handleIdentifierChange = React.useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      setIdentifiers(event.target.value);
      setResults([]);
      identifierRequestVersionRef.current += 1;
      setPendingAction(null);
    },
    [],
  );

  const handlePdfFiles = React.useCallback(async (selectedFiles: File[]) => {
    identifierRequestVersionRef.current += 1;
    setPendingAction(null);
    const validation = validatePdfSelection(selectedFiles, {
      maxFiles: MAX_UPLOAD_FILES_PER_SELECTION,
      allowMultiple: true,
    });

    if (!validation.ok) {
      setUploadStatus('error');
      setUploadMessage(validation.error ?? 'Please select PDF files only.');
      return;
    }

    setUploadStatus('uploading');
    setUploadMessage(`Uploading ${validation.files.length} PDF${validation.files.length === 1 ? '' : 's'}...`);

    let succeeded = 0;
    const failures: string[] = [];
    const uploadResults: LiteratureImportResult[] = [];

    for (const file of validation.files) {
      try {
        const documentId = await uploadPdfDocument(file);
        succeeded += 1;
        uploadResults.push({
          identifier: file.name,
          normalizedIdentifier: null,
          status: 'imported',
          message: 'Queued for background processing',
          documentId,
          filename: file.name,
          source: {
            provider: 'manual_upload',
            viewerMode: 'local_pdf',
            pdfArtifactId: 'uploaded PDF',
            sourceMd5: 'pending',
          },
        });
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Upload failed';
        failures.push(`${file.name}: ${message}`);
        uploadResults.push({
          identifier: file.name,
          normalizedIdentifier: null,
          status: 'invalid',
          message,
        });
      }
    }

    setResults(uploadResults);

    if (succeeded > 0) {
      emitGlobalToast({
        message: 'Your PDFs are processing in the background. You can safely navigate away.',
        severity: 'info',
        autoHideDurationMs: 6000,
        anchorOrigin: { vertical: 'bottom', horizontal: 'left' },
      });
      void refreshJobs(true);
    }

    if (failures.length > 0) {
      setUploadStatus(succeeded > 0 ? 'complete' : 'error');
      setUploadMessage(`Queued ${succeeded}/${validation.files.length} PDFs. ${failures.slice(0, 2).join(' | ')}`);
      return;
    }

    setUploadStatus('complete');
    setUploadMessage(`Queued ${succeeded} PDF${succeeded === 1 ? '' : 's'} for background processing.`);
  }, [refreshJobs]);

  const handleFileSelect = React.useCallback(
    async (event: React.ChangeEvent<HTMLInputElement>) => {
      await handlePdfFiles(Array.from(event.target.files ?? []));
      event.target.value = '';
    },
    [handlePdfFiles],
  );

  const handleUploadDrop = React.useCallback(
    async (event: React.DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      await handlePdfFiles(Array.from(event.dataTransfer.files ?? []));
    },
    [handlePdfFiles],
  );

  return (
    <Box sx={{ flex: '1 1 auto', minHeight: 0, overflow: 'auto', pr: { xs: 0, md: 1 } }}>
      <Stack spacing={2.5} sx={{ pb: 3 }}>
        <Paper
          variant="outlined"
          sx={{
            p: { xs: 2, md: 2.5 },
            borderRadius: 1,
            borderColor: alpha(theme.palette.primary.main, 0.22),
            background: `linear-gradient(135deg, ${alpha(theme.palette.background.paper, 0.98)}, ${alpha(theme.palette.primary.main, 0.04)})`,
          }}
        >
          <Stack spacing={2}>
            <Stack
              direction={{ xs: 'column', md: 'row' }}
              justifyContent="space-between"
              alignItems={{ xs: 'flex-start', md: 'center' }}
              spacing={1.5}
            >
              <Box>
                <Typography variant="h4" component="h1" sx={{ fontWeight: 700 }}>
                  Add Literature
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5, maxWidth: 720 }}>
                  Add PDFs directly, or resolve source identifiers and retrieve the PDF when the catalog can provide it.
                </Typography>
              </Box>
              <Chip
                icon={<ArticleIcon />}
                label="Library intake"
                color="success"
                variant="outlined"
                sx={{ borderRadius: 1 }}
              />
            </Stack>

            <Tabs
              value={mode}
              onChange={handleModeChange}
              aria-label="Literature import mode"
              variant="fullWidth"
            >
              <Tab value="identifiers" icon={<SearchIcon fontSize="small" />} iconPosition="start" label="Identifiers" />
              <Tab value="upload" icon={<CloudUploadIcon fontSize="small" />} iconPosition="start" label="Upload PDFs" />
            </Tabs>

            {mode === 'identifiers' ? (
              <>
                <Box>
                  <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
                    Add Publication by Identifiers
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    Resolve identifiers first, then import the resolved PDF-backed publications when the batch looks right.
                  </Typography>
                </Box>
                <TextField
                  label="Source identifiers"
                  value={identifiers}
                  onChange={handleIdentifierChange}
                  multiline
                  minRows={5}
                  fullWidth
                  placeholder={`PMID:23970418
PubMed ID 23970418
AGRKB:101000000055784`}
                  helperText="PMID, PubMed ID, AGRKB, or ABC identifiers; comma or newline separated. PMCID/FBrf can be added when backend resolution supports them."
                  InputProps={{
                    sx: {
                      alignItems: 'flex-start',
                      fontFamily: 'monospace',
                      fontSize: '0.92rem',
                    },
                  }}
                />
                <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} alignItems={{ xs: 'stretch', sm: 'center' }}>
                  <Button
                    variant="outlined"
                    startIcon={<SearchIcon />}
                    onClick={handleResolve}
                    disabled={isWorking || !identifiers.trim()}
                    sx={{ minWidth: 120 }}
                  >
                    Resolve
                  </Button>
                  <Button
                    variant="contained"
                    startIcon={<PlayArrowIcon />}
                    onClick={handleResolveAndImport}
                    disabled={isWorking || !identifiers.trim()}
                    sx={{ minWidth: 160 }}
                  >
                    Resolve and Import
                  </Button>
                  <Button variant="outlined" startIcon={<RefreshIcon />} onClick={handleReset} disabled={isWorking}>
                    Reset
                  </Button>
                  {isWorking && (
                    <Stack direction="row" spacing={1} alignItems="center" sx={{ minWidth: 180 }}>
                      <LinearProgress sx={{ width: 96 }} />
                      <Typography variant="body2" color="text.secondary">
                        {pendingAction === 'resolve' ? 'Resolving identifiers' : 'Starting import jobs'}
                      </Typography>
                    </Stack>
                  )}
                </Stack>
                <Alert severity="info" sx={{ borderRadius: 1 }}>
                  Resolve is a dry run. Resolve and Import starts PDF-backed jobs that continue in the background and remain visible in PDF Jobs.
                </Alert>
              </>
            ) : (
              <Paper
                variant="outlined"
                sx={{
                  p: 2,
                  borderRadius: 1,
                  borderStyle: 'dashed',
                  borderColor: alpha(theme.palette.primary.main, 0.45),
                  bgcolor: alpha(theme.palette.primary.main, 0.04),
                }}
              >
                <Stack spacing={1.5}>
                  <Box>
                    <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
                      Upload PDFs
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      Uploaded PDFs still use the existing processing path when no source md5 match is found.
                    </Typography>
                  </Box>
                  <Paper
                    variant="outlined"
                    onDragOver={(event) => event.preventDefault()}
                    onDrop={handleUploadDrop}
                    sx={{
                      p: { xs: 2, md: 3 },
                      borderRadius: 1,
                      borderStyle: 'dashed',
                      borderColor: alpha(theme.palette.primary.main, 0.55),
                      bgcolor: alpha(theme.palette.background.paper, 0.55),
                      textAlign: 'center',
                    }}
                  >
                    <Stack spacing={1.25} alignItems="center">
                      <CloudUploadIcon color="primary" />
                      <Typography variant="body2" sx={{ fontWeight: 700 }}>
                        Drop PDFs here or choose files
                      </Typography>
                      <Typography variant="caption" color="text.secondary" sx={{ maxWidth: 560 }}>
                        Processing continues in the background, with progress visible in PDF Jobs and final documents available in the Library.
                      </Typography>
                      <Button variant="contained" component="label" startIcon={<CloudUploadIcon />} disabled={isUploading}>
                        Choose PDFs
                        <input hidden multiple type="file" accept="application/pdf" onChange={handleFileSelect} />
                      </Button>
                    </Stack>
                  </Paper>
                  {uploadMessage && (
                    <Alert
                      severity={uploadStatus === 'error' ? 'error' : uploadStatus === 'complete' ? 'success' : 'info'}
                      sx={{ borderRadius: 1 }}
                    >
                      {uploadMessage}
                    </Alert>
                  )}
                </Stack>
              </Paper>
            )}
          </Stack>
        </Paper>

        <PdfJobsPanel jobs={jobs} loading={jobsLoading} onCancelJob={handleCancelJob} />

        <Paper variant="outlined" sx={{ borderRadius: 1, overflow: 'hidden' }}>
          <Box sx={{ px: 2, py: 1.5, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <Box>
              <Typography variant="h6" sx={{ fontWeight: 700 }}>
                Import Results
              </Typography>
              <Typography variant="body2" color="text.secondary">
                PDF-backed source retrievals and upload jobs land in the Library.
              </Typography>
            </Box>
            <Chip
              label={`${results.length} result${results.length === 1 ? '' : 's'}`}
              variant="outlined"
              sx={{ borderRadius: 1 }}
            />
          </Box>
          <Divider />

          <TableContainer sx={{ maxHeight: { xs: 520, md: 620 } }}>
            <Table stickyHeader size="small" aria-label="Literature import results">
              <TableHead>
                <TableRow>
                  <TableCell>Identifier</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell>Document</TableCell>
                  <TableCell>Source</TableCell>
                  <TableCell align="right">Action</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {results.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={5}>
                      <Typography variant="body2" color="text.secondary">
                        No import results yet.
                      </Typography>
                    </TableCell>
                  </TableRow>
                ) : results.map((result) => {
                  const tone = statusTone[result.status];
                  const canOpen = canOpenInDocuments(result);

                  return (
                    <TableRow key={`${result.identifier}-${result.status}-${result.documentId ?? result.jobId ?? result.message}`} hover>
                      <TableCell>
                        <Stack spacing={0.25}>
                          <Typography variant="body2" sx={{ fontFamily: 'monospace', fontWeight: 700 }}>
                            {result.identifier}
                          </Typography>
                          <Typography variant="caption" color="text.secondary">
                            {result.normalizedIdentifier ?? 'No normalized identifier'}
                          </Typography>
                        </Stack>
                      </TableCell>
                      <TableCell>
                        <Stack spacing={0.75}>
                          <Chip
                            icon={tone.icon}
                            label={tone.label}
                            color={tone.chipColor}
                            size="small"
                            variant={result.status === 'imported' || result.status === 'resolved' ? 'filled' : 'outlined'}
                            sx={{ alignSelf: 'flex-start', borderRadius: 1 }}
                          />
                          <Typography variant="caption" color="text.secondary">
                            {result.message}
                          </Typography>
                        </Stack>
                      </TableCell>
                      <TableCell>
                        {result.filename ? (
                          <Stack spacing={0.25}>
                            <Typography variant="body2" sx={{ fontWeight: 700 }}>
                              {result.filename}
                            </Typography>
                            <Typography variant="caption" color="text.secondary">
                              {result.jobId
                                ? `Job ${result.jobId}`
                                : result.status === 'duplicate' && result.documentId
                                  ? 'Existing document'
                                  : result.status === 'resolved'
                                    ? 'Ready to import'
                                    : result.status === 'imported'
                                      ? result.source?.provider === 'manual_upload'
                                        ? 'Queued upload'
                                        : 'Queued document'
                                      : result.documentId
                                        ? 'Document available'
                                        : 'Document pending'}
                            </Typography>
                          </Stack>
                        ) : (
                          <Typography variant="body2" color="text.secondary">
                            Not queued
                          </Typography>
                        )}
                      </TableCell>
                      <TableCell>
                        {result.source ? (
                          <Stack spacing={0.25}>
                            <Typography variant="caption" sx={{ fontFamily: 'monospace' }}>
                              PDF {result.source.pdfArtifactId} / {convertedSourceLabel(result)}
                            </Typography>
                            <Typography variant="caption" color="text.secondary">
                              {result.source.viewerMode} - {result.source.chunks ?? 'pending'} chunks
                            </Typography>
                          </Stack>
                        ) : (
                          <Typography variant="body2" color="text.secondary">
                            No local PDF
                          </Typography>
                        )}
                      </TableCell>
                      <TableCell align="right">
                        <Tooltip title={canOpen ? 'View in Library' : result.status === 'resolved' ? 'Ready to import' : 'No PDF-backed document available'}>
                          <span>
                            <IconButton
                              size="small"
                              color="success"
                              disabled={!canOpen}
                              onClick={() => handleViewDocument(result)}
                              aria-label={`View ${result.filename ?? result.identifier} in Library`}
                            >
                              <FileOpenIcon fontSize="small" />
                            </IconButton>
                          </span>
                        </Tooltip>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </TableContainer>
        </Paper>
      </Stack>
    </Box>
  );
};

export default AddLiteraturePage;
