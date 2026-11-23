import { Alert, Box, Chip, CircularProgress, Divider, Paper, Stack, Typography } from '@mui/material'
import { useQuery } from '@tanstack/react-query'

import { fetchViewerFiles, fileDisplayLabel, isPdfFile, type ViewerFile } from '@/components/pdfViewer/viewerFiles'
import { fetchPaperSourceFiles, type PaperSourceFile } from '@/features/documents/paperSourceFiles'

import {
  LISTING_REFUSAL_TEXT,
  OBSERVATION_TEXT,
  duplicateCopiesText,
  fewCharactersNote,
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

function SourceFileRow({ file, provider }: { file: PaperSourceFile; provider: string }) {
  const observation = file.last_provider_observation
  const fewCharacters = file.check.few_characters_count
  return (
    <Box component="li" sx={{ py: 0.5 }}>
      <Typography variant="body2" sx={{ fontWeight: 600, overflowWrap: 'anywhere' }}>
        {file.display_name || 'Unnamed file'}
      </Typography>
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
    queryKey: ['paper-source-files', documentId],
    queryFn: ({ signal }) => fetchPaperSourceFiles(documentId, signal),
    enabled: open,
    retry: false,
    refetchOnWindowFocus: false,
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
    return <Alert severity="warning">{`Could not check ${provider} for this paper's files.`}</Alert>
  }
  const result = listingQuery.data
  if (result.kind === 'refused') {
    const text = LISTING_REFUSAL_TEXT[result.code]
    return text ? <Alert severity="info">{text(provider)}</Alert> : null
  }
  const others = result.listing.files.filter((file) => !file.in_this_paper)
  return (
    <Box>
      <Typography variant="subtitle2" component="h3" gutterBottom>
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
    queryKey: ['paper-files', documentId],
    queryFn: ({ signal }) => fetchViewerFiles(documentId, signal),
    enabled: open,
    retry: false,
    refetchOnWindowFocus: false,
  })

  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Typography variant="subtitle2" component="h2" gutterBottom>
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
