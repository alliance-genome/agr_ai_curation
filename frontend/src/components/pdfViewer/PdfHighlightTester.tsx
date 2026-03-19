import { useEffect, useState } from 'react'
import { Alert, Box, Button, Divider, Stack, TextField, Typography } from '@mui/material'

import {
  dispatchApplyHighlights,
  dispatchClearHighlights,
  dispatchClearSnippetLocalization,
  dispatchLocateSnippet,
  onClearSnippetLocalization,
  onSnippetLocalizationResult,
  SnippetLocalizationResultDetail,
} from '@/components/pdfViewer/pdfEvents'

function PdfHighlightTester() {
  const [term, setTerm] = useState('')
  const [snippet, setSnippet] = useState('')
  const [localizationResult, setLocalizationResult] = useState<SnippetLocalizationResultDetail | null>(null)

  useEffect(() => {
    const stopResultListener = onSnippetLocalizationResult((event) => {
      setLocalizationResult(event.detail)
    })

    const stopClearListener = onClearSnippetLocalization(() => {
      setLocalizationResult(null)
    })

    return () => {
      stopResultListener()
      stopClearListener()
    }
  }, [])

  const handleHighlight = () => {
    const trimmed = term.trim()
    if (!trimmed) {
      return
    }

    dispatchApplyHighlights(`manual-highlight-${Date.now()}`, [trimmed])
  }

  const handleLocateSnippet = () => {
    const trimmed = snippet.trim()
    if (!trimmed) {
      return
    }

    setLocalizationResult(null)
    dispatchLocateSnippet(window.crypto.randomUUID(), trimmed)
  }

  const handleClear = () => {
    dispatchClearHighlights('user-action')
    dispatchClearSnippetLocalization('user-action')
    setLocalizationResult(null)
    window.dispatchEvent(new CustomEvent('pdf-overlay-clear'))
  }

  const renderLocalizationResult = () => {
    if (!localizationResult) {
      return null
    }

    const renderedPages = localizationResult.renderedPages.length > 0
      ? localizationResult.renderedPages.join(', ')
      : 'none'

    const severity = localizationResult.status === 'success'
      ? 'success'
      : localizationResult.status === 'not-ready'
        ? 'warning'
        : 'info'

    return (
      <Alert severity={severity}>
        <Stack spacing={0.75}>
          <Typography variant="body2">
            {localizationResult.status === 'success'
              ? `Found ${localizationResult.matchCount} rendered match${localizationResult.matchCount === 1 ? '' : 'es'} for the snippet.`
              : localizationResult.status === 'not-ready'
                ? (localizationResult.reason ?? 'Viewer is not ready for snippet localization yet.')
                : localizationResult.reason ?? 'Snippet was not found in the currently rendered text layers.'}
          </Typography>
          {localizationResult.selectedMatch && (
            <Typography variant="caption" color="text.secondary">
              Showing match {localizationResult.selectedMatch.index + 1} on page
              {localizationResult.selectedMatch.pages.length === 1 ? '' : 's'} {localizationResult.selectedMatch.pages.join(', ')}
              {' '}with {localizationResult.selectedMatch.rectCount} rect{localizationResult.selectedMatch.rectCount === 1 ? '' : 's'}.
            </Typography>
          )}
          <Typography variant="caption" color="text.secondary">
            Rendered pages scanned: {localizationResult.renderedPageCount}/{localizationResult.totalPageCount} ({renderedPages}).
            Search time: {localizationResult.durationMs.toFixed(1)} ms.
          </Typography>
        </Stack>
      </Alert>
    )
  }

  return (
    <Stack spacing={2}>
      <Typography variant="h6">Highlight Tester</Typography>
      <Typography variant="body2" color="text.secondary">
        Use these controls to probe the iframe viewer without changing the production evidence UX. Manual term highlighting still uses
        `mark.js`; the snippet control below runs the sentence-localization spike against the rendered PDF.js text layer.
      </Typography>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} alignItems={{ xs: 'stretch', sm: 'center' }}>
        <TextField
          label="Term"
          size="small"
          value={term}
          onChange={(event) => setTerm(event.target.value)}
          fullWidth
        />
        <Button variant="contained" onClick={handleHighlight} sx={{ minWidth: 120 }}>
          Highlight
        </Button>
      </Stack>
      <Divider />
      <Stack spacing={1.5}>
        <Typography variant="subtitle2">Sentence localization spike</Typography>
        <TextField
          label="Snippet"
          size="small"
          value={snippet}
          onChange={(event) => setSnippet(event.target.value)}
          placeholder="Paste an exact sentence or phrase from the PDF text layer"
          multiline
          minRows={2}
          fullWidth
        />
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
          <Button variant="contained" color="secondary" onClick={handleLocateSnippet} sx={{ minWidth: 180 }}>
            Locate exact snippet
          </Button>
          <Button variant="outlined" onClick={handleClear} sx={{ minWidth: 160 }}>
            Clear viewer probes
          </Button>
        </Stack>
        {renderLocalizationResult()}
      </Stack>
      <Box>
        <Typography variant="caption" color="text.secondary">
          The snippet probe searches only the rendered text layers that currently exist in the iframe. If the PDF.js viewer has not rendered
          a page yet, the result reports that coverage so we can judge whether bbox or text-content fallback is still needed.
        </Typography>
      </Box>
    </Stack>
  )
}

export default PdfHighlightTester
