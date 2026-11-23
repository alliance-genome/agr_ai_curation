import { useState } from 'react'
import { Box, Button, Typography } from '@mui/material'
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined'

import SetAsideRecordsDialog from './SetAsideRecordsDialog'
import {
  type OutputSetAsideCounts,
  type SetAsideOutputNoun,
  outputSetAsideLabel,
} from './setAsideSummary'

interface OutputSetAsideNoticeProps {
  counts: OutputSetAsideCounts
  noun: SetAsideOutputNoun
  /** Names the output for the button's accessible name and the viewer subtitle. */
  outputName: string
  outputId: string | null
  flowRunId: string | null
  documentId: string | null
}

/**
 * One line under a chat output (a file card or a chat table) whose sources hold
 * records the review set aside, with a View button when the run and paper are
 * known. Read only.
 */
export default function OutputSetAsideNotice({
  counts,
  noun,
  outputName,
  outputId,
  flowRunId,
  documentId,
}: OutputSetAsideNoticeProps) {
  const [viewerOpen, setViewerOpen] = useState(false)
  const leftOut = counts.left_out > 0
  const canView = Boolean(flowRunId && documentId && outputId)

  return (
    <>
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 0.75,
          mt: 1,
          pt: 1,
          borderTop: 1,
          borderColor: 'divider',
          color: leftOut ? 'warning.main' : 'text.secondary',
        }}
      >
        <InfoOutlinedIcon aria-hidden sx={{ fontSize: '1rem' }} />
        <Typography variant="caption" sx={{ flex: 1, fontWeight: leftOut ? 600 : 400 }}>
          {outputSetAsideLabel(counts, noun)}
        </Typography>
        {canView && (
          <Button
            size="small"
            variant="text"
            color={leftOut ? 'warning' : 'primary'}
            aria-label={`View set-aside records for ${outputName}`}
            onClick={() => setViewerOpen(true)}
            sx={{ textTransform: 'none', fontWeight: 600, minWidth: 0, py: 0 }}
          >
            View
          </Button>
        )}
      </Box>
      {flowRunId && documentId && outputId && (
        <SetAsideRecordsDialog
          open={viewerOpen}
          onClose={() => setViewerOpen(false)}
          flowName={null}
          papers={[{
            flowRunId,
            documentId,
            outputId,
            title: outputName,
            count: counts.included,
            placement: 'included',
          }]}
        />
      )}
    </>
  )
}
