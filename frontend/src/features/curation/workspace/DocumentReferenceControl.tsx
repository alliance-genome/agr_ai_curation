import { useCallback, useState } from 'react'
import type { ChangeEvent, FormEvent } from 'react'
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Stack,
  TextField,
  Typography,
} from '@mui/material'

import {
  findDocumentReference,
  linkDocumentReference,
  unlinkDocumentReference,
} from '@/features/curation/services/curationWorkspaceService'
import type {
  CurationDocumentRef,
  CurationDocumentReferenceResult,
} from '@/features/curation/types'
import type { DocumentSourceProviderMetadata } from '@/services/weaviate'

export interface DocumentReferenceControlProps {
  document: CurationDocumentRef
  importEnabled: boolean
  onChanged: () => Promise<void> | void
  presentation: DocumentSourceProviderMetadata | null
  sessionId: string
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'The request failed. Try again.'
}

function ComparedPaper({ heading, lines }: { heading: string; lines: Array<string | null | undefined> }) {
  return (
    <Stack spacing={0.25} sx={{ flex: 1, minWidth: 0 }}>
      <Typography variant="overline" sx={{ color: 'text.secondary', lineHeight: 1.4 }}>
        {heading}
      </Typography>
      {lines.filter(Boolean).map((line) => (
        <Typography key={line} variant="body2" sx={{ overflowWrap: 'anywhere' }}>
          {line}
        </Typography>
      ))}
    </Stack>
  )
}

interface LinkDialogProps {
  document: CurationDocumentRef
  label: string
  onCancel: () => void
  onLinked: () => Promise<void> | void
  open: boolean
  presentation: DocumentSourceProviderMetadata | null
  sessionId: string
}

function LinkDialog({
  document,
  label,
  onCancel,
  onLinked,
  open,
  presentation,
  sessionId,
}: LinkDialogProps) {
  const [identifier, setIdentifier] = useState('')
  const [found, setFound] = useState<CurationDocumentReferenceResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isBusy, setIsBusy] = useState(false)
  const examples = presentation?.identifierExamples?.slice(0, 2).join(', ')

  const handleFind = useCallback(async (event: FormEvent) => {
    event.preventDefault()
    const trimmed = identifier.trim()
    if (!trimmed || isBusy) {
      return
    }
    setIsBusy(true)
    setError(null)
    setFound(null)
    try {
      const result = await findDocumentReference(sessionId, trimmed)
      if (result.status === 'found') {
        setFound(result)
      } else {
        setError(result.message)
      }
    } catch (findError) {
      setError(errorMessage(findError))
    } finally {
      setIsBusy(false)
    }
  }, [identifier, isBusy, sessionId])

  const handleConfirm = useCallback(async () => {
    setIsBusy(true)
    setError(null)
    try {
      const result = await linkDocumentReference(sessionId, identifier.trim())
      if (result.status !== 'linked') {
        setFound(null)
        setError(result.message)
        return
      }
      await onLinked()
    } catch (linkError) {
      setFound(null)
      setError(errorMessage(linkError))
    } finally {
      setIsBusy(false)
    }
  }, [identifier, onLinked, sessionId])

  return (
    <Dialog fullWidth maxWidth="sm" onClose={isBusy ? undefined : onCancel} open={open}>
      <DialogTitle>Link to {label}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 0.5 }}>
          <DialogContentText variant="body2">
            Enter the paper&apos;s identifier in {label}. You will see the paper it names before
            anything is saved.
          </DialogContentText>
          <Stack component="form" direction="row" onSubmit={handleFind} spacing={1}>
            <TextField
              autoFocus
              fullWidth
              helperText={examples ? `For example: ${examples}` : undefined}
              label="Identifier"
              onChange={(event: ChangeEvent<HTMLInputElement>) => {
                setIdentifier(event.target.value)
                setFound(null)
                setError(null)
              }}
              size="small"
              value={identifier}
            />
            <Button
              disabled={isBusy || identifier.trim() === ''}
              sx={{ alignSelf: 'flex-start', height: 40 }}
              type="submit"
              variant="outlined"
            >
              Find
            </Button>
          </Stack>
          {error ? <Alert severity="warning">{error}</Alert> : null}
          {found ? (
            <Stack spacing={1.5}>
              <Typography variant="body2">Is this the same paper?</Typography>
              <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
                <ComparedPaper
                  heading="This document"
                  lines={[document.title, document.filename === document.title ? null : document.filename]}
                />
                <ComparedPaper
                  heading={`Found in ${label}`}
                  lines={[found.reference_title, found.reference_curie]}
                />
              </Stack>
            </Stack>
          ) : null}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button disabled={isBusy} onClick={onCancel}>
          Cancel
        </Button>
        {found ? (
          <Button disabled={isBusy} onClick={() => void handleConfirm()} variant="contained">
            Link this paper
          </Button>
        ) : null}
      </DialogActions>
    </Dialog>
  )
}

export default function DocumentReferenceControl({
  document,
  importEnabled,
  onChanged,
  presentation,
  sessionId,
}: DocumentReferenceControlProps) {
  const [dialog, setDialog] = useState<'link' | 'unlink' | null>(null)
  const [unlinkError, setUnlinkError] = useState<string | null>(null)
  const [isUnlinking, setIsUnlinking] = useState(false)
  const label = presentation?.displayLabel ?? 'the literature source'
  const reference = document.reference_curie
  const canChange = importEnabled

  const handleLinked = useCallback(async () => {
    await onChanged()
    setDialog(null)
  }, [onChanged])

  const handleUnlink = useCallback(async () => {
    setIsUnlinking(true)
    setUnlinkError(null)
    try {
      await unlinkDocumentReference(sessionId)
      await onChanged()
      setDialog(null)
    } catch (error) {
      setUnlinkError(errorMessage(error))
    } finally {
      setIsUnlinking(false)
    }
  }, [onChanged, sessionId])

  if (!reference && !canChange) {
    return null
  }

  return (
    <Stack
      data-testid="document-reference"
      direction="row"
      spacing={1}
      sx={{ alignItems: 'center', minHeight: 24 }}
    >
      <Typography variant="caption" sx={{ color: reference ? 'text.secondary' : 'warning.main' }}>
        {reference
          ? `${presentation?.displayLabel ?? 'Reference'}: ${reference}`
          : `Not linked to ${label}`}
      </Typography>
      {!reference ? (
        <Button
          aria-label={`Link to ${label}`}
          onClick={() => setDialog('link')}
          size="small"
          sx={{ minWidth: 0, py: 0, fontSize: '0.72rem' }}
        >
          Link
        </Button>
      ) : null}
      {reference && canChange && document.reference_source === 'curator_link' ? (
        <Button
          onClick={() => {
            setUnlinkError(null)
            setDialog('unlink')
          }}
          size="small"
          sx={{ minWidth: 0, py: 0, fontSize: '0.72rem' }}
        >
          Unlink
        </Button>
      ) : null}
      {dialog === 'link' ? (
        <LinkDialog
          document={document}
          label={label}
          onCancel={() => setDialog(null)}
          onLinked={handleLinked}
          open
          presentation={presentation}
          sessionId={sessionId}
        />
      ) : null}
      <Dialog
        fullWidth
        maxWidth="xs"
        onClose={isUnlinking ? undefined : () => setDialog(null)}
        open={dialog === 'unlink'}
      >
        <DialogTitle>Unlink this paper?</DialogTitle>
        <DialogContent>
          <Stack spacing={2}>
            <DialogContentText variant="body2">
              This removes the link to {reference}. The curated records stay as they are.
            </DialogContentText>
            {unlinkError ? <Alert severity="warning">{unlinkError}</Alert> : null}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button disabled={isUnlinking} onClick={() => setDialog(null)}>
            Cancel
          </Button>
          <Button disabled={isUnlinking} onClick={() => void handleUnlink()} variant="contained">
            Unlink this paper
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  )
}
