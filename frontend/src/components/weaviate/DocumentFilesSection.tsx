import { Alert, Box, Chip, CircularProgress, Divider, Paper, Stack, Typography } from '@mui/material'
import { useQuery } from '@tanstack/react-query'

import {
  UNNAMED_FILE_LABEL,
  fetchViewerFiles,
  fileDisplayLabel,
  isPdfFile,
  type ViewerFile,
} from '@/components/pdfViewer/viewerFiles'
import {
  PaperSourceFilesContractError,
  fetchPaperSourceFiles,
  type PaperSourceListedFile,
} from '@/features/documents/paperSourceFiles'
import type { DocumentSourceProvenance } from '@/services/weaviate'

import {
  LISTING_UNREADABLE_TEXT,
  OBSERVATION_TEXT,
  duplicateCopiesText,
  fewCharactersNote,
  listingRefusalText,
  notShownText,
  sourceFileStatusText,
} from './paperFileReasonText'

/**
 * The paper's files in its details dialog (ALL-1460, P10b-ui part a; read only).
 *
 * The paper's own files come from the viewer's files route. For a paper imported from
 * a provider, the provider's other files for it come from the per-paper listing, each
 * with why it is not in the paper (codes worded in `paperFileReasonText.ts`).
 * Adding and removing files are later parts.
 *
 * All texts are proposals awaiting sign-off.
 */

/** Query keys, shared with the dialog's "Refresh details". */
export const paperFilesQueryKey = (documentId: string) => ['paper-files', documentId] as const
export const paperSourceFilesQueryKey = (documentId: string) => ['paper-source-files', documentId] as const

/**
 * Reopening the same paper within this time shows the stored listing without asking the
 * provider again (each listing reads the provider and can download up to 20 converted
 * texts). "Refresh details" always asks again.
 */
export const PAPER_SOURCE_FILES_STALE_MS = 5 * 60 * 1000

/**
 * The provider's label for the Files section: only its configured label. The label is
 * required when a provider is registered (the backend refuses to start without one), so
 * a paper whose provider has no label came from a provider this server does not have; the
 * listing would refuse it (`not_imported_from_provider`), so nothing is asked or shown.
 * Never the raw provider id.
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

interface DocumentFilesSectionProps {
  documentId: string
  /** The provider's display label when the paper was imported from one; null for an upload. */
  providerLabel: string | null
  open: boolean
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

function SourceFileRow({ file, provider }: { file: PaperSourceListedFile; provider: string }) {
  const observation = file.recognized ? file.last_provider_observation : null
  const fewCharacters = file.recognized ? file.check.few_characters_count : null
  return (
    <Box component="li" sx={{ py: 0.5 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
        <Typography variant="body2" sx={{ fontWeight: 600, overflowWrap: 'anywhere' }}>
          {file.display_name || UNNAMED_FILE_LABEL}
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

function ProviderFilesPart({ documentId, provider, open }: { documentId: string; provider: string; open: boolean }) {
  const listingQuery = useQuery({
    queryKey: paperSourceFilesQueryKey(documentId),
    queryFn: ({ signal }) => fetchPaperSourceFiles(documentId, signal),
    enabled: open,
    retry: false,
    refetchOnWindowFocus: false,
    staleTime: PAPER_SOURCE_FILES_STALE_MS,
  })

  if (listingQuery.isPending) {
    return (
      <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
        <CircularProgress size={14} />
        <Typography variant="body2">{`Checking ${provider} for this paper's files...`}</Typography>
      </Stack>
    )
  }
  if (listingQuery.isError) {
    return (
      <Alert severity="warning">
        {listingQuery.error instanceof PaperSourceFilesContractError
          ? LISTING_UNREADABLE_TEXT(provider)
          : `Could not check ${provider} for this paper's files.`}
      </Alert>
    )
  }
  const result = listingQuery.data
  if (result.kind === 'refused') {
    const text = listingRefusalText(result.code, result.httpStatus, provider)
    return text ? <Alert severity="info">{text}</Alert> : null
  }
  const others = result.listing.files.filter((file) => !file.in_this_paper)
  return (
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
          {others.map((file) => <SourceFileRow key={file.provider_file_id} file={file} provider={provider} />)}
        </Box>
      )}
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
  )
}

export default function DocumentFilesSection({ documentId, providerLabel, open }: DocumentFilesSectionProps) {
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
          <Alert severity="warning">Could not load this paper&apos;s files.</Alert>
        ) : (
          <Box component="ul" aria-label="Files in this paper" sx={{ m: 0, pl: 2.5 }}>
            {filesQuery.data.files.map((file) => <PaperFileRow key={file.file_id} file={file} />)}
          </Box>
        )}
        {providerLabel ? <ProviderFilesPart documentId={documentId} provider={providerLabel} open={open} /> : null}
      </Stack>
    </Paper>
  )
}
