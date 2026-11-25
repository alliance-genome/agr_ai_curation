import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Box, Button, Checkbox, Chip, CircularProgress, Divider, Paper, Stack, Typography } from '@mui/material'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { showPaperFilesInViewer } from '@/components/pdfViewer/pdfEvents'
import {
  UNNAMED_FILE_LABEL,
  fetchViewerFiles,
  fileDisplayLabel,
  isPdfFile,
  type ViewerFile,
} from '@/components/pdfViewer/viewerFiles'
import { PDF_PROGRESS_POLL_INTERVAL_MS } from '@/features/documents/documentIntakeConfig'
import {
  PaperSourceFilesContractError,
  fetchPaperSourceFiles,
  type PaperSourceFile,
  type PaperSourceListedFile,
} from '@/features/documents/paperSourceFiles'
import { parseSupplementJobProgress } from '@/features/documents/supplementProgress'
import {
  AddDocumentSourceFilesError,
  ApiHttpError,
  AuthenticationError,
  addDocumentSourceFiles,
  fetchActivePdfJobsForDocument,
  fetchDocumentSourceProviderConfiguration,
  fetchPdfJob,
  isActivePdfJobStatus,
  type DocumentSourceProvenance,
  type PdfProcessingJob,
} from '@/services/weaviate'

import SupplementFilesList from './SupplementFilesList'
import {
  LIBRARY_ADD_TEXT,
  LISTING_UNREADABLE_TEXT,
  OBSERVATION_TEXT,
  TRY_AGAIN_BUTTON_TEXT,
  addFilesButtonText,
  addFilesRefusalText,
  duplicateCopiesText,
  fewCharactersNote,
  listingRefusalText,
  notShownText,
  sourceFileStatusText,
  uploadedPaperFilesText,
} from './paperFileReasonText'

/**
 * The paper's files in its details dialog (ALL-1460 / ALL-1747, P10b-ui parts a and b).
 *
 * The paper's own files come from the viewer's files route. For a paper imported from
 * a provider, the provider's other files for it come from the per-paper listing, each
 * with why it is not in the paper (codes worded in `paperFileReasonText.ts`), and
 * converted supplements can be ticked and added here (with per-file job progress via
 * `SupplementFilesList` and refresh of both lists and an open viewer for this paper).
 * Removing files is a separate part (Part 5).
 */

/** Query keys, shared with the dialog's "Refresh details". */
export const paperFilesQueryKey = (documentId: string) => ['paper-files', documentId] as const
export const paperSourceFilesQueryKey = (documentId: string) => ['paper-source-files', documentId] as const
export const paperActiveJobsQueryKey = (documentId: string) => ['paper-active-jobs', documentId] as const
export const documentSourceProviderConfigQueryKey = () =>
  ['document-source-provider-configuration'] as const

/**
 * Reopening the same paper within this time shows the stored listing without asking the
 * provider again (each listing reads the provider and can download up to 20 converted
 * texts). "Refresh details" always asks again.
 */
export const PAPER_SOURCE_FILES_STALE_MS = 5 * 60 * 1000

/**
 * Retained in-flight add job ids keyed by document id so closing the dialog mid-add and
 * reopening the same paper reconciles the completed job and busts the 5-minute listing cache.
 */
const pendingAddJobIdsByDocument = new Map<string, string>()

/** Clear retained add job ids (used for test isolation). */
export function clearPendingAddJobIds(): void {
  pendingAddJobIdsByDocument.clear()
}

/**
 * The provider's label for the Files section: only its configured label. The label is
 * required when a provider is registered (the backend refuses to start without one), so
 * a paper whose provider has no label came from a provider this server does not have; the
 * listing would refuse it (`not_imported_from_provider`), so nothing is asked of the
 * provider listing. Never the raw provider id.
 */
export function listedProviderLabel(
  source: Pick<DocumentSourceProvenance, 'provider' | 'providerMetadata'> | null,
): string | null {
  if (!source?.provider) {
    return null
  }
  return source.providerMetadata?.displayLabel ?? null
}

const ROLE_TEXT: Record<ViewerFile['role'], string> = {
  main: 'Main paper',
  supplement: 'Supplement',
}

const STATE_TEXT: Record<ViewerFile['status'], string> = {
  ready: 'Ready',
  pending: 'Being added',
  failed: 'Could not be added',
}

function formatObservedDate(value: string): string {
  return new Date(value).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })
}

function isAbortError(error: unknown): boolean {
  return (
    typeof error === 'object'
    && error !== null
    && 'name' in error
    && (error as { name?: unknown }).name === 'AbortError'
  )
}

function isSupplementAddJob(job: PdfProcessingJob | null): boolean {
  return job !== null && job.metadata?.job_kind === 'supplement_add'
}

type TickablePaperSourceFile = Extract<PaperSourceFile, { tickable: true }>

function isTickableOtherFile(file: PaperSourceListedFile): file is TickablePaperSourceFile {
  return file.recognized && file.tickable && !file.in_this_paper
}

interface AddRefusalBanner {
  code: string | null
  text: string
  retryable: boolean
}

interface TrackedJobState {
  job: PdfProcessingJob
  origin: 'user_add' | 'server_active'
}

export interface DocumentFilesSectionProps {
  documentId: string
  /** The provider's display label when the paper was imported from one; null for an upload. */
  providerLabel: string | null
  /** The provider's short label when configured (e.g. `providerMetadata.shortLabel`); falls back to `providerLabel`. */
  providerShortLabel?: string | null
  /** Optional override for the server's configured provider short label on uploaded papers (Card 34). */
  configuredProviderShortLabel?: string | null
  /** The paper's processing status; Library Add is enabled only when `completed`. */
  processingStatus?: string | null
  open: boolean
  /** Called after an active or submitted supplement job finishes so parent document details can refresh. */
  onFilesChanged?: () => void
  /** Optional poll interval override (defaults to env-configured `PDF_PROGRESS_POLL_INTERVAL_MS`). */
  pollIntervalMs?: number
}

function PaperFileRow({ file }: { file: ViewerFile }) {
  return (
    <Box component="li" sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', py: 0.5 }}>
      <Typography variant="body2" sx={{ fontWeight: 600, overflowWrap: 'anywhere' }}>
        {fileDisplayLabel(file)}
      </Typography>
      <Chip size="small" variant="outlined" label={ROLE_TEXT[file.role]} />
      <Chip size="small" variant="outlined" label={isPdfFile(file) ? 'PDF' : 'Text'} />
      <Chip
        size="small"
        label={STATE_TEXT[file.status]}
        color={file.status === 'ready' ? 'success' : file.status === 'failed' ? 'error' : 'default'}
      />
    </Box>
  )
}

function SourceFileRow({
  file,
  provider,
  canSelect,
  selected,
  selectionDisabled,
  onToggleSelect,
}: {
  file: PaperSourceListedFile
  provider: string
  canSelect: boolean
  selected: boolean
  selectionDisabled: boolean
  onToggleSelect: (providerFileId: string) => void
}) {
  const fileName = file.display_name || UNNAMED_FILE_LABEL
  const observation = file.recognized ? file.last_provider_observation : null
  const fewCharacters = file.recognized ? file.check.few_characters_count : null
  return (
    <Box component="li" sx={{ py: 0.5 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
        {canSelect ? (
          <Checkbox
            size="small"
            checked={selected}
            disabled={selectionDisabled}
            onChange={() => onToggleSelect(file.provider_file_id)}
            slotProps={{ input: { 'aria-label': fileName } }}
            sx={{ p: 0.25 }}
          />
        ) : null}
        <Typography variant="body2" sx={{ fontWeight: 600, overflowWrap: 'anywhere' }}>
          {fileName}
        </Typography>
        {file.recognized && file.file_role === 'main' ? (
          <Chip size="small" variant="outlined" label={ROLE_TEXT.main} />
        ) : null}
      </Box>
      <Typography variant="body2" color="text.secondary">{sourceFileStatusText(file, provider)}</Typography>
      {fewCharacters !== null ? (
        <Typography variant="body2" color="text.secondary">{fewCharactersNote(fewCharacters)}</Typography>
      ) : null}
      {observation ? (
        <Typography variant="body2" color="text.secondary">
          {OBSERVATION_TEXT[observation.code](provider, formatObservedDate(observation.observed_at))}
        </Typography>
      ) : null}
    </Box>
  )
}

function UploadedPaperFilesNotice({
  configuredProviderShortLabel,
  open,
}: {
  configuredProviderShortLabel?: string | null
  open: boolean
}) {
  const providerConfigQuery = useQuery({
    queryKey: documentSourceProviderConfigQueryKey(),
    queryFn: ({ signal }) => fetchDocumentSourceProviderConfiguration({ signal }),
    enabled: open && configuredProviderShortLabel === undefined,
    retry: false,
    refetchOnWindowFocus: false,
    staleTime: PAPER_SOURCE_FILES_STALE_MS,
  })

  if (configuredProviderShortLabel !== undefined) {
    return configuredProviderShortLabel ? (
      <Typography variant="body2" color="text.secondary">
        {uploadedPaperFilesText(configuredProviderShortLabel)}
      </Typography>
    ) : null
  }

  if (!providerConfigQuery.data?.importEnabled) {
    return null
  }
  const shortLabel =
    providerConfigQuery.data.presentation?.shortLabel
    ?? providerConfigQuery.data.presentation?.displayLabel
  if (!shortLabel) {
    return null
  }
  return (
    <Typography variant="body2" color="text.secondary">
      {uploadedPaperFilesText(shortLabel)}
    </Typography>
  )
}

function ProviderFilesPart({
  documentId,
  provider,
  providerShortLabel,
  processingStatus,
  open,
  onFilesChanged,
  pollIntervalMs,
}: {
  documentId: string
  provider: string
  providerShortLabel?: string | null
  processingStatus?: string | null
  open: boolean
  onFilesChanged?: () => void
  pollIntervalMs: number
}) {
  const queryClient = useQueryClient()
  const shortProvider = providerShortLabel?.trim() || provider
  const isPaperCompleted =
    processingStatus === undefined
      ? true
      : typeof processingStatus === 'string' && processingStatus.toLowerCase() === 'completed'

  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set())
  const [submitting, setSubmitting] = useState(false)
  const [addRefusal, setAddRefusal] = useState<AddRefusalBanner | null>(null)
  const [importDisabled, setImportDisabled] = useState(false)
  const [trackedJobState, setTrackedJobState] = useState<TrackedJobState | null>(null)
  const [reconcilingRetainedJob, setReconcilingRetainedJob] = useState(
    () => open && pendingAddJobIdsByDocument.has(documentId),
  )
  const [retainedRetryCount, setRetainedRetryCount] = useState(0)
  const [pollError, setPollError] = useState<string | null>(null)
  const [pollRetryCount, setPollRetryCount] = useState(0)

  const mountedRef = useRef(true)
  const activeDocumentIdRef = useRef(documentId)
  activeDocumentIdRef.current = documentId
  const onFilesChangedRef = useRef(onFilesChanged)
  onFilesChangedRef.current = onFilesChanged
  const submitAbortControllerRef = useRef<AbortController | null>(null)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      submitAbortControllerRef.current?.abort()
      submitAbortControllerRef.current = null
    }
  }, [])

  // Reset transient selection, progress, and refusal state when switching papers or closing the dialog.
  useEffect(() => {
    submitAbortControllerRef.current?.abort()
    submitAbortControllerRef.current = null
    setSelectedIds(new Set())
    setSubmitting(false)
    setAddRefusal(null)
    setImportDisabled(false)
    setTrackedJobState(null)
    setPollError(null)
    setPollRetryCount(0)
  }, [documentId, open])

  const listingQuery = useQuery({
    queryKey: paperSourceFilesQueryKey(documentId),
    queryFn: ({ signal }) => fetchPaperSourceFiles(documentId, signal),
    enabled: open,
    retry: false,
    refetchOnWindowFocus: false,
    staleTime: PAPER_SOURCE_FILES_STALE_MS,
  })

  const activeJobsQuery = useQuery({
    queryKey: paperActiveJobsQueryKey(documentId),
    queryFn: ({ signal }) => fetchActivePdfJobsForDocument(documentId, { signal }),
    enabled: open,
    retry: false,
    refetchOnWindowFocus: false,
  })

  const refetchActiveJobsRef = useRef(activeJobsQuery.refetch)
  refetchActiveJobsRef.current = activeJobsQuery.refetch

  const serverActiveJob = useMemo(
    () => activeJobsQuery.data?.find((job) => isActivePdfJobStatus(job.status)) ?? null,
    [activeJobsQuery.data],
  )

  const handleTrackedJobFinished = useCallback(async (targetDocumentId: string) => {
    const [viewerResult] = await Promise.allSettled([
      queryClient.fetchQuery({
        queryKey: paperFilesQueryKey(targetDocumentId),
        queryFn: ({ signal }) => fetchViewerFiles(targetDocumentId, signal),
        staleTime: 0,
      }),
      queryClient.invalidateQueries({ queryKey: paperSourceFilesQueryKey(targetDocumentId) }),
      queryClient.invalidateQueries({ queryKey: paperActiveJobsQueryKey(targetDocumentId) }),
    ])
    if (!mountedRef.current || activeDocumentIdRef.current !== targetDocumentId) {
      return
    }
    setAddRefusal((prev) => (prev?.code === 'import_in_progress' ? null : prev))
    if (viewerResult.status === 'fulfilled') {
      showPaperFilesInViewer(targetDocumentId, viewerResult.value.files, { refreshOnlyIfOpen: true })
    }
    onFilesChangedRef.current?.()
  }, [queryClient])

  const handleTrackedJobFinishedRef = useRef(handleTrackedJobFinished)
  handleTrackedJobFinishedRef.current = handleTrackedJobFinished

  // Reconcile a retained add job started before the dialog closed so reopening within the
  // 5-minute listing cache window never re-offers files that already finished adding.
  useEffect(() => {
    if (!open) {
      setReconcilingRetainedJob(false)
      return
    }
    const retainedJobId = pendingAddJobIdsByDocument.get(documentId)
    if (!retainedJobId) {
      setReconcilingRetainedJob(false)
      return
    }
    let cancelled = false
    const controller = new AbortController()
    const targetDocumentId = documentId
    setReconcilingRetainedJob(true)

    void (async () => {
      try {
        const latest = await fetchPdfJob(retainedJobId, { signal: controller.signal })
        if (cancelled || controller.signal.aborted || latest.document_id !== targetDocumentId) {
          return
        }
        setAddRefusal((prev) => (prev?.code === 'import_in_progress' ? null : prev))
        setPollError(null)
        setTrackedJobState({ job: latest, origin: 'user_add' })
        if (!isActivePdfJobStatus(latest.status)) {
          if (pendingAddJobIdsByDocument.get(targetDocumentId) === retainedJobId) {
            pendingAddJobIdsByDocument.delete(targetDocumentId)
          }
          await handleTrackedJobFinishedRef.current(targetDocumentId)
        }
      } catch (error) {
        if (cancelled || controller.signal.aborted || isAbortError(error)) {
          return
        }
        if (error instanceof ApiHttpError && error.httpStatus === 404) {
          if (pendingAddJobIdsByDocument.get(targetDocumentId) === retainedJobId) {
            pendingAddJobIdsByDocument.delete(targetDocumentId)
          }
          await handleTrackedJobFinishedRef.current(targetDocumentId)
        } else {
          setPollError('Could not check progress for adding files.')
        }
      } finally {
        if (!cancelled && !controller.signal.aborted) {
          setReconcilingRetainedJob(false)
        }
      }
    })()

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [open, documentId, retainedRetryCount])

  // Adopt an existing server-side active job (e.g. waiting_for_supplements or an add started earlier).
  useEffect(() => {
    if (!open || !serverActiveJob) {
      return
    }
    setAddRefusal((prev) => (prev?.code === 'import_in_progress' ? null : prev))
    setTrackedJobState((current) => {
      if (current && current.job.job_id === serverActiveJob.job_id) {
        return current
      }
      if (current && isActivePdfJobStatus(current.job.status)) {
        return current
      }
      return {
        job: serverActiveJob,
        origin: isSupplementAddJob(serverActiveJob) ? 'user_add' : 'server_active',
      }
    })
  }, [open, serverActiveJob])

  const trackedJob = trackedJobState?.job ?? null
  const trackedJobOrigin = trackedJobState?.origin ?? null
  const trackedJobId = trackedJob?.job_id ?? null
  const isTrackedJobActive = trackedJob !== null && isActivePdfJobStatus(trackedJob.status)

  useEffect(() => {
    if (!open || !trackedJobId || !isTrackedJobActive) {
      return
    }
    let cancelled = false
    let timerId: number | null = null
    const controller = new AbortController()
    const polledDocumentId = documentId

    const pollOnce = async () => {
      try {
        const latest = await fetchPdfJob(trackedJobId, { signal: controller.signal })
        if (cancelled || controller.signal.aborted || latest.document_id !== polledDocumentId) {
          return
        }
        setTrackedJobState((current) =>
          current && current.job.job_id === latest.job_id
            ? { ...current, job: latest }
            : current,
        )
        setPollError(null)
        if (!isActivePdfJobStatus(latest.status)) {
          if (pendingAddJobIdsByDocument.get(polledDocumentId) === trackedJobId) {
            pendingAddJobIdsByDocument.delete(polledDocumentId)
          }
          await handleTrackedJobFinishedRef.current(polledDocumentId)
          return
        }
      } catch (error) {
        if (cancelled || controller.signal.aborted || isAbortError(error)) {
          return
        }
        if (error instanceof ApiHttpError && error.httpStatus === 404) {
          if (pendingAddJobIdsByDocument.get(polledDocumentId) === trackedJobId) {
            pendingAddJobIdsByDocument.delete(polledDocumentId)
          }
          try {
            const refetched = await refetchActiveJobsRef.current()
            if (cancelled || controller.signal.aborted) {
              return
            }
            const remainingActive =
              refetched.data?.find((job) => isActivePdfJobStatus(job.status)) ?? null
            if (remainingActive && remainingActive.job_id !== trackedJobId) {
              setTrackedJobState({
                job: remainingActive,
                origin: isSupplementAddJob(remainingActive) ? 'user_add' : 'server_active',
              })
              setPollError(null)
            } else {
              setTrackedJobState(null)
              setPollError(null)
              await handleTrackedJobFinishedRef.current(polledDocumentId)
            }
          } catch {
            if (!cancelled && !controller.signal.aborted) {
              setTrackedJobState(null)
              setPollError('Could not check progress for adding files.')
            }
          }
          return
        }
        setPollError('Could not check progress for adding files.')
        return
      }
      if (!cancelled) {
        timerId = window.setTimeout(() => {
          void pollOnce()
        }, pollIntervalMs)
      }
    }

    timerId = window.setTimeout(() => {
      void pollOnce()
    }, pollIntervalMs)

    return () => {
      cancelled = true
      controller.abort()
      if (timerId !== null) {
        window.clearTimeout(timerId)
      }
    }
  }, [open, documentId, trackedJobId, isTrackedJobActive, pollIntervalMs, pollRetryCount])

  const listingResult = listingQuery.data
  const others = useMemo(
    () => (listingResult?.kind === 'listing' ? listingResult.listing.files.filter((file) => !file.in_this_paper) : []),
    [listingResult],
  )
  const tickableOthers = useMemo(
    () => others.filter(isTickableOtherFile),
    [others],
  )

  const selectedFiles = useMemo(() => {
    const seen = new Set<string>()
    const chosen: TickablePaperSourceFile[] = []
    for (const file of tickableOthers) {
      if (selectedIds.has(file.provider_file_id) && !seen.has(file.provider_file_id)) {
        seen.add(file.provider_file_id)
        chosen.push(file)
      }
    }
    return chosen
  }, [selectedIds, tickableOthers])

  const isAddTrackedJob =
    trackedJob !== null
    && (trackedJobOrigin === 'user_add' || isSupplementAddJob(trackedJob))
  const serverJobStillActive =
    serverActiveJob !== null
    && !(
      trackedJob !== null
      && trackedJob.job_id === serverActiveJob.job_id
      && !isTrackedJobActive
    )
  const hasActiveJobForPaper = serverJobStillActive || isTrackedJobActive
  const showImportRunningNotice =
    (serverJobStillActive && !isSupplementAddJob(serverActiveJob))
    || (trackedJob !== null && !isAddTrackedJob && isTrackedJobActive)
  const showTickboxes =
    isPaperCompleted
    && !hasActiveJobForPaper
    && !activeJobsQuery.isPending
    && !activeJobsQuery.isError
    && !reconcilingRetainedJob

  const handleToggleSelect = useCallback((providerFileId: string) => {
    setAddRefusal(null)
    setSelectedIds((previous) => {
      const next = new Set(previous)
      if (next.has(providerFileId)) {
        next.delete(providerFileId)
      } else {
        next.add(providerFileId)
      }
      return next
    })
  }, [])

  const handleAddSelected = useCallback(async () => {
    if (!isPaperCompleted || selectedFiles.length === 0 || submitting || hasActiveJobForPaper || activeJobsQuery.isError) {
      return
    }
    submitAbortControllerRef.current?.abort()
    const controller = new AbortController()
    submitAbortControllerRef.current = controller

    setSubmitting(true)
    setAddRefusal(null)
    setPollError(null)

    try {
      const acceptedJob = await addDocumentSourceFiles(
        documentId,
        {
          files: selectedFiles.map(({ provider_file_id, md5, converted_md5 }) => ({
            provider_file_id,
            md5,
            converted_md5,
          })),
        },
        { signal: controller.signal },
      )
      if (controller.signal.aborted || !mountedRef.current || activeDocumentIdRef.current !== documentId) {
        return
      }
      setSelectedIds(new Set())
      setTrackedJobState({ job: acceptedJob, origin: 'user_add' })
      setSubmitting(false)
      if (isActivePdfJobStatus(acceptedJob.status)) {
        pendingAddJobIdsByDocument.set(documentId, acceptedJob.job_id)
      } else {
        pendingAddJobIdsByDocument.delete(documentId)
        await handleTrackedJobFinishedRef.current(documentId)
      }
    } catch (error) {
      if (
        controller.signal.aborted
        || !mountedRef.current
        || activeDocumentIdRef.current !== documentId
        || isAbortError(error)
      ) {
        return
      }
      setSubmitting(false)
      if (error instanceof AuthenticationError) {
        const signInText =
          listingRefusalText('document_source_curator_token_unavailable', 409, provider)
          ?? 'Sign in again to add files to this paper.'
        setAddRefusal({ code: 'authentication_required', text: signInText, retryable: false })
        return
      }
      if (error instanceof AddDocumentSourceFilesError) {
        if (error.code === 'document_source_import_disabled') {
          setImportDisabled(true)
          void queryClient.invalidateQueries({ queryKey: paperSourceFilesQueryKey(documentId) })
          return
        }
        if (error.code !== null) {
          const text = addFilesRefusalText(error.code, {
            provider,
            providerShortLabel: shortProvider,
            httpStatus: error.httpStatus,
            limitValue: error.limitValue,
          })
          if (error.code === 'import_in_progress') {
            void queryClient.invalidateQueries({ queryKey: paperActiveJobsQueryKey(documentId) })
          }
          if (text !== null) {
            const retryable =
              error.code === 'file_removal_unfinished'
              || (error.code === 'document_source_curator_token_unavailable' && error.httpStatus === 503)
            setAddRefusal({ code: error.code, text, retryable })
          }
          return
        }
      }
      setAddRefusal({ code: null, text: 'Could not add files to this paper.', retryable: true })
    } finally {
      if (submitAbortControllerRef.current === controller) {
        submitAbortControllerRef.current = null
      }
    }
  }, [
    activeJobsQuery.isError,
    documentId,
    hasActiveJobForPaper,
    isPaperCompleted,
    provider,
    queryClient,
    selectedFiles,
    shortProvider,
    submitting,
  ])

  const handleRetryPoll = useCallback(() => {
    setPollError(null)
    setPollRetryCount((count) => count + 1)
  }, [])

  const handleRetryRetainedOrActiveJobs = useCallback(() => {
    setPollError(null)
    setRetainedRetryCount((count) => count + 1)
    void activeJobsQuery.refetch()
  }, [activeJobsQuery])

  if (importDisabled) {
    return null
  }

  if (listingQuery.isPending) {
    return (
      <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
        <CircularProgress size={14} />
        <Typography variant="body2">{`Checking ${provider} for this paper's files...`}</Typography>
      </Stack>
    )
  }
  if (listingQuery.isError) {
    const isContractError = listingQuery.error instanceof PaperSourceFilesContractError
    return (
      <Alert
        severity="warning"
        action={
          isContractError ? undefined : (
            <Button size="small" color="inherit" onClick={() => void listingQuery.refetch()}>
              {TRY_AGAIN_BUTTON_TEXT}
            </Button>
          )
        }
      >
        {isContractError
          ? LISTING_UNREADABLE_TEXT(provider)
          : `Could not check ${provider} for this paper's files.`}
      </Alert>
    )
  }

  const result = listingQuery.data
  if (result.kind === 'refused') {
    const text = result.code === 'not_imported_from_provider'
      ? uploadedPaperFilesText(shortProvider)
      : listingRefusalText(result.code, result.httpStatus, provider)
    return text ? <Alert severity="info">{text}</Alert> : null
  }

  const trackedProgress = trackedJob
    ? parseSupplementJobProgress(trackedJob.metadata, { currentStage: trackedJob.current_stage })
    : null
  const showRefusalAlert =
    addRefusal !== null
    && !(showImportRunningNotice && addRefusal.code === 'import_in_progress')
  const canRetryAddRefusal = Boolean(
    addRefusal?.retryable && selectedFiles.length > 0 && showTickboxes && !submitting,
  )
  const terminalAddAlertText =
    isAddTrackedJob && !isTrackedJobActive && (trackedJob.status === 'failed' || trackedJob.status === 'cancelled')
      ? trackedJob.error_message || trackedJob.message || (trackedJob.status === 'cancelled' ? 'Cancelled' : 'Adding files failed.')
      : null

  return (
    <Stack spacing={1.5}>
      {showImportRunningNotice ? (
        <Alert severity="info">{LIBRARY_ADD_TEXT.importStillRunning}</Alert>
      ) : null}

      {activeJobsQuery.isError && !hasActiveJobForPaper ? (
        <Alert
          severity="warning"
          action={(
            <Button size="small" color="inherit" onClick={() => void activeJobsQuery.refetch()}>
              {TRY_AGAIN_BUTTON_TEXT}
            </Button>
          )}
        >
          Could not check whether this paper has an active import.
        </Alert>
      ) : null}

      {trackedJob ? (
        isAddTrackedJob ? (
          <Stack spacing={1} aria-live="polite">
            <Typography variant="subtitle2">
              {LIBRARY_ADD_TEXT.addingFilesHeading}
            </Typography>
            {isTrackedJobActive ? (
              <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
                <CircularProgress size={14} />
                <Typography variant="body2" color="text.secondary">
                  {trackedJob.message || 'Adding files...'}
                </Typography>
              </Stack>
            ) : terminalAddAlertText ? (
              <Alert severity={trackedJob.status === 'failed' ? 'warning' : 'info'}>
                {terminalAddAlertText}
              </Alert>
            ) : trackedJob.message ? (
              <Typography variant="body2" color="text.secondary">
                {trackedJob.message}
              </Typography>
            ) : null}
            <SupplementFilesList
              progress={trackedProgress}
              currentStage={trackedJob.current_stage}
              providerShortLabel={shortProvider}
              providerLabel={provider}
            />
            {pollError ? (
              <Alert
                severity="warning"
                action={(
                  <Button size="small" color="inherit" onClick={handleRetryPoll}>
                    {TRY_AGAIN_BUTTON_TEXT}
                  </Button>
                )}
              >
                {pollError}
              </Alert>
            ) : null}
          </Stack>
        ) : (trackedProgress || pollError) ? (
          <Stack spacing={1}>
            <SupplementFilesList
              progress={trackedProgress}
              currentStage={trackedJob.current_stage}
              providerShortLabel={shortProvider}
              providerLabel={provider}
            />
            {pollError ? (
              <Alert
                severity="warning"
                action={(
                  <Button size="small" color="inherit" onClick={handleRetryPoll}>
                    {TRY_AGAIN_BUTTON_TEXT}
                  </Button>
                )}
              >
                {pollError}
              </Alert>
            ) : null}
          </Stack>
        ) : null
      ) : pollError ? (
        <Alert
          severity="warning"
          action={(
            <Button size="small" color="inherit" onClick={handleRetryRetainedOrActiveJobs}>
              {TRY_AGAIN_BUTTON_TEXT}
            </Button>
          )}
        >
          {pollError}
        </Alert>
      ) : null}

      {showRefusalAlert ? (
        <Alert
          severity="warning"
          action={
            canRetryAddRefusal ? (
              <Button size="small" color="inherit" onClick={() => void handleAddSelected()}>
                {TRY_AGAIN_BUTTON_TEXT}
              </Button>
            ) : undefined
          }
        >
          {addRefusal.text}
        </Alert>
      ) : null}

      <Box>
        <Typography variant="subtitle2" gutterBottom>
          {`Also in ${provider}`}
        </Typography>
        {others.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            {`${provider} has no other files for this paper.`}
          </Typography>
        ) : (
          <Box component="ul" aria-label={`Files in ${provider} not in this paper`} sx={{ m: 0, pl: 2.5 }}>
            {others.map((file) => {
              const canSelect = showTickboxes && isTickableOtherFile(file)
              return (
                <SourceFileRow
                  key={file.provider_file_id}
                  file={file}
                  provider={provider}
                  canSelect={canSelect}
                  selected={canSelect && selectedIds.has(file.provider_file_id)}
                  selectionDisabled={submitting}
                  onToggleSelect={handleToggleSelect}
                />
              )
            })}
          </Box>
        )}
        {showTickboxes && tickableOthers.length > 0 ? (
          <Box sx={{ mt: 1.5 }}>
            <Button
              variant="contained"
              size="small"
              disabled={selectedFiles.length === 0 || submitting}
              onClick={() => void handleAddSelected()}
            >
              {addFilesButtonText(selectedFiles.length)}
            </Button>
          </Box>
        ) : null}
        {result.listing.not_shown_count > 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            {notShownText(result.listing.not_shown_count, provider)}
          </Typography>
        ) : null}
        {result.listing.duplicate_copies_count > 0 ? (
          <Typography variant="body2" color="text.secondary">
            {duplicateCopiesText(result.listing.duplicate_copies_count)}
          </Typography>
        ) : null}
      </Box>
    </Stack>
  )
}

export default function DocumentFilesSection({
  documentId,
  providerLabel,
  providerShortLabel,
  configuredProviderShortLabel,
  processingStatus,
  open,
  onFilesChanged,
  pollIntervalMs = PDF_PROGRESS_POLL_INTERVAL_MS,
}: DocumentFilesSectionProps) {
  const filesQuery = useQuery({
    queryKey: paperFilesQueryKey(documentId),
    queryFn: ({ signal }) => fetchViewerFiles(documentId, signal),
    enabled: open,
    retry: false,
    refetchOnWindowFocus: false,
  })

  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Typography variant="subtitle2" gutterBottom>
        Files
      </Typography>
      <Divider sx={{ mb: 2 }} />
      <Stack spacing={2}>
        {filesQuery.isPending ? (
          <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
            <CircularProgress size={14} />
            <Typography variant="body2">Loading this paper&apos;s files...</Typography>
          </Stack>
        ) : filesQuery.isError ? (
          <Alert
            severity="warning"
            action={(
              <Button size="small" color="inherit" onClick={() => void filesQuery.refetch()}>
                {TRY_AGAIN_BUTTON_TEXT}
              </Button>
            )}
          >
            Could not load this paper&apos;s files.
          </Alert>
        ) : (
          <Box component="ul" aria-label="Files in this paper" sx={{ m: 0, pl: 2.5 }}>
            {filesQuery.data.files.map((file) => <PaperFileRow key={file.file_id} file={file} />)}
          </Box>
        )}
        {providerLabel ? (
          <ProviderFilesPart
            key={documentId}
            documentId={documentId}
            provider={providerLabel}
            providerShortLabel={providerShortLabel}
            processingStatus={processingStatus}
            open={open}
            onFilesChanged={onFilesChanged}
            pollIntervalMs={pollIntervalMs}
          />
        ) : (
          <UploadedPaperFilesNotice
            configuredProviderShortLabel={configuredProviderShortLabel}
            open={open}
          />
        )}
      </Stack>
    </Paper>
  )
}
