import { useState } from 'react'
import { Box, Button, Stack, TextField, Typography } from '@mui/material'

import { dispatchApplyHighlights, dispatchClearHighlights } from '@/components/pdfViewer/pdfEvents'

function PdfHighlightTester() {
  const [term, setTerm] = useState('')

  const handleHighlight = () => {
    const trimmed = term.trim()
    if (!trimmed) {
      return
    }

    dispatchApplyHighlights(`manual-highlight-${Date.now()}`, [trimmed])
  }

  const handleClear = () => {
    dispatchClearHighlights('user-action')
    window.dispatchEvent(new CustomEvent('pdf-overlay-clear'))
  }

  return (
    <Stack spacing={2}>
      <Typography variant="h6">Highlight Tester</Typography>
      <Typography variant="body2" color="text.secondary">
        Type a word or phrase and press highlight to see it marked inside the PDF viewer. Use Clear to remove manual highlights.
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
        <Button variant="outlined" onClick={handleClear} sx={{ minWidth: 120 }}>
          Clear
        </Button>
      </Stack>
      <Box>
        <Typography variant="caption" color="text.secondary">
          Manual highlights replace the current set of highlighted terms. Use this tool to verify mark.js integration independently of chat responses.
        </Typography>
      </Box>
    </Stack>
  )
}

export default PdfHighlightTester
