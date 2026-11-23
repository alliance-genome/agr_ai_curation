import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Alert, Box, Button, CircularProgress, Stack, Typography } from '@mui/material'

import type { PdfViewerNavigationResult } from './pdfEvidenceNavigation'
import {
  buildTextNavigationResult,
  commandQuote,
  locateEvidenceInBlocks,
  locateSectionInBlocks,
  type TextNavigationRequest,
  type TextQuoteBlockSpan,
} from './viewerFileNavigation'
import { fetchViewerText, type DisplayBlock, type ViewerFile } from './viewerFiles'
import { TextFileBlocks } from './TextFileBlocks'

export const VIEWER_FILE_PANEL_ID = 'viewer-file-panel'

/** Approved by Chris (23 Nov 2026). */
const LOADING_TEXT = 'Loading the file\'s text...'
const LOAD_ERROR_TEXT = 'Could not load this file\'s text.'

export function viewerFileTabId(fileId: string): string {
  return `viewer-file-tab-${fileId}`
}

interface TextFileViewProps {
  documentId: string
  file: ViewerFile
  navigation: TextNavigationRequest | null
  onNavigationResult: (request: TextNavigationRequest, result: PdfViewerNavigationResult) => void
}

type TextLoadState =
  | { kind: 'loading' }
  | { kind: 'ready'; blocks: DisplayBlock[] }
  | { kind: 'error' }

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/**
 * A text file of the paper (a converted Word or Excel supplement, or a text-imported
 * paper), rendered from the server's display blocks. Evidence is located by exact
 * copied text only (see viewerFileNavigation.ts).
 */
export function TextFileView({ documentId, file, navigation, onNavigationResult }: TextFileViewProps) {
  const fileId = file.file_id
  const textUrl = file.text_url
  const [loadState, setLoadState] = useState<TextLoadState>({ kind: 'loading' })
  const [reloadKey, setReloadKey] = useState(0)
  const [highlight, setHighlight] = useState<TextQuoteBlockSpan[] | null>(null)
  // The evidence's section, shown when its quote is not found.
  const [sectionBlockIndex, setSectionBlockIndex] = useState<number | null>(null)
  const highlightedRef = useRef<HTMLElement | null>(null)
  // Each navigation request is located and reported once.
  const reportedRequestIdRef = useRef<number | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    setLoadState({ kind: 'loading' })
    setHighlight(null)
    setSectionBlockIndex(null)
    fetchViewerText({ fileId, textUrl }, controller.signal)
      .then((payload) => {
        if (!controller.signal.aborted) {
          setLoadState({ kind: 'ready', blocks: payload.blocks })
        }
      })
      .catch((error) => {
        if (controller.signal.aborted) {
          return
        }
        console.warn('Unable to load a text file for the viewer', { fileId, error })
        setLoadState({ kind: 'error' })
      })
    return () => controller.abort()
    // Keyed on the file's id and text address only: a refreshed file list with the
    // same file must not fetch again or drop the highlight.
  }, [fileId, textUrl, reloadKey])

  useEffect(() => {
    if (
      !navigation
      || navigation.fileId !== fileId
      || loadState.kind !== 'ready'
      || reportedRequestIdRef.current === navigation.requestId
    ) {
      return
    }
    reportedRequestIdRef.current = navigation.requestId
    const { textEvidence } = navigation.command
    const location = locateEvidenceInBlocks(loadState.blocks, textEvidence, commandQuote(navigation.command))
    const sectionIndex = location.found > 0 ? null : locateSectionInBlocks(loadState.blocks, textEvidence)
    setHighlight(location.found > 0 ? location.blocks : null)
    setSectionBlockIndex(sectionIndex)
    onNavigationResult(navigation, buildTextNavigationResult(navigation, documentId, location, sectionIndex))
  }, [documentId, fileId, loadState, navigation, onNavigationResult])

  useEffect(() => {
    if ((highlight || sectionBlockIndex !== null) && highlightedRef.current) {
      highlightedRef.current.scrollIntoView({
        block: 'center',
        behavior: prefersReducedMotion() ? 'auto' : 'smooth',
      })
    }
  }, [highlight, sectionBlockIndex])

  const setHighlightedElement = useCallback((element: HTMLElement | null) => {
    highlightedRef.current = element
  }, [])

  let content: ReactNode
  if (loadState.kind === 'loading') {
    content = (
      <Stack direction="row" spacing={1} sx={{ alignItems: 'center', p: 2 }}>
        <CircularProgress size={16} />
        <Typography variant="body2">{LOADING_TEXT}</Typography>
      </Stack>
    )
  } else if (loadState.kind === 'error') {
    content = (
      <Alert
        severity="error"
        sx={{ m: 2 }}
        action={(
          <Button color="inherit" size="small" onClick={() => setReloadKey((key) => key + 1)}>
            Try again
          </Button>
        )}
      >
        {LOAD_ERROR_TEXT}
      </Alert>
    )
  } else {
    content = (
      <TextFileBlocks
        blocks={loadState.blocks}
        highlightedBlocks={highlight}
        sectionBlockIndex={sectionBlockIndex}
        firstHighlightRef={setHighlightedElement}
      />
    )
  }

  return (
    <Box
      role="tabpanel"
      id={VIEWER_FILE_PANEL_ID}
      aria-labelledby={viewerFileTabId(file.file_id)}
      data-testid="viewer-text-file"
      sx={{
        position: 'absolute',
        inset: 0,
        overflow: 'auto',
        px: 2,
        py: 1.5,
        backgroundColor: 'background.paper',
        color: 'text.primary',
        '& table': { borderCollapse: 'collapse', my: 1.5, fontSize: '0.85rem' },
        '& th, & td': { border: 1, borderColor: 'divider', px: 1, py: 0.5, textAlign: 'left', verticalAlign: 'top' },
        '& pre': { whiteSpace: 'pre-wrap', fontSize: '0.85rem' },
      }}
    >
      {content}
    </Box>
  )
}
