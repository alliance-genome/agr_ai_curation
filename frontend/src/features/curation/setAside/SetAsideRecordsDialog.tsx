import React from 'react'
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
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
  IconButton,
  Paper,
  Stack,
  Typography,
} from '@mui/material'
import {
  Close,
  InfoOutlined as InfoOutlinedIcon,
  ExpandMore as ExpandMoreIcon,
} from '@mui/icons-material'

import { fetchFlowRunSetAsideRecords } from '@/features/curation/services/curationWorkspaceService'
import type {
  FlowRunSetAsideRecordsResponse,
  SetAsideRecord,
  SetAsideResult,
} from '@/features/curation/types'
import {
  type FlowSetAsideSummary,
  type SetAsidePlacement,
  SET_ASIDE_ALREADY_IN_FILE,
  SET_ASIDE_INCLUDE_ADVICE,
  SET_ASIDE_READ_ONLY_NOTE,
  SET_ASIDE_UNPROFILED_NOTE,
  isLeftOutSomewhere,
  recordCountText,
  setAsidePlacement,
  setAsidePlacementTag,
} from './setAsideSummary'

/** One paper of a finished run whose review set records aside. */
export interface SetAsidePaper {
  documentId: string
  flowRunId: string
  /** The paper's title; null where the page shows only one paper and has no title for it. */
  title: string | null
  summary: FlowSetAsideSummary
}

interface SetAsideRecordsDialogProps {
  open: boolean
  onClose: () => void
  flowName: string
  papers: SetAsidePaper[]
}

type PaperLoad =
  | { state: 'loading' }
  | { state: 'error'; message: string }
  | { state: 'loaded'; data: FlowRunSetAsideRecordsResponse }

const paperKey = (paper: SetAsidePaper) => `${paper.flowRunId}:${paper.documentId}`

function renderValue(value: unknown): string {
  if (value === null) return '—'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}

function resultHeading(result: SetAsideResult): string | null {
  if (result.agent_name) return result.agent_name
  if (result.step !== null) return `Step ${result.step}`
  return null
}

function RecordPanel({
  record,
  index,
  placementTag,
}: {
  record: SetAsideRecord
  index: number
  placementTag: string | null
}) {
  return (
    <Paper variant="outlined" sx={{ p: 2 }} component="section" aria-label={`Record ${index}`}>
      <Stack
        direction="row"
        spacing={1}
        useFlexGap
        sx={{ alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', mb: 1.5 }}
      >
        <Typography variant="subtitle2" component="h3">
          Record {index}
          {record.label && (
            <Typography component="span" variant="body2" sx={{ color: 'text.secondary', ml: 1 }}>
              {record.label}
            </Typography>
          )}
        </Typography>
        <Stack direction="row" spacing={1}>
          <Chip
            size="small"
            variant="outlined"
            color="warning"
            icon={<InfoOutlinedIcon />}
            label={`Review status: ${record.review_status}`}
          />
          {placementTag && <Chip size="small" variant="outlined" label={placementTag} />}
        </Stack>
      </Stack>
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
          columnGap: 2,
          rowGap: 1.5,
        }}
      >
        {record.fields.map((field, fieldIndex) => (
          <Box key={`${field.field_ref ?? field.label}-${fieldIndex}`} sx={{ minWidth: 0 }}>
            <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block' }}>
              {field.label}
            </Typography>
            <Typography variant="body2" sx={{ overflowWrap: 'anywhere' }}>
              {renderValue(field.value)}
            </Typography>
          </Box>
        ))}
      </Box>
      <Divider sx={{ my: 1.5 }} />
      <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block' }}>
        Review reason
      </Typography>
      <Typography variant="body2">
        {record.review_reason ?? 'The review saved no reason for this record.'}
      </Typography>
    </Paper>
  )
}

function PaperRecords({
  load,
  placement,
  onRetry,
}: {
  load: PaperLoad
  placement: SetAsidePlacement
  onRetry: () => void
}) {
  if (load.state === 'loading') {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
        <CircularProgress size={28} thickness={4} aria-label="Loading set-aside records" />
      </Box>
    )
  }
  if (load.state === 'error') {
    return (
      <Alert
        severity="error"
        action={<Button color="inherit" size="small" onClick={onRetry}>Try again</Button>}
      >
        The set-aside records could not be loaded. {load.message}
      </Alert>
    )
  }
  const { results } = load.data
  if (results.length === 0) {
    return (
      <Typography variant="body2" sx={{ color: 'text.secondary', py: 1 }}>
        No set-aside records are waiting for this paper. They may have been decided in Review &amp; Curate.
      </Typography>
    )
  }
  const showHeadings = results.length > 1
  return (
    <Stack spacing={2}>
      {results.map((result) => {
        const heading = showHeadings ? resultHeading(result) : null
        const tag = result.review_fields_available ? setAsidePlacementTag(placement) : 'Not in the file'
        return (
          <Stack key={result.extraction_result_id} spacing={1.5}>
            {heading && (
              <Typography variant="subtitle2" component="h3" sx={{ color: 'text.secondary' }}>
                {heading}
              </Typography>
            )}
            {!result.review_fields_available && (
              <Alert severity="info">{SET_ASIDE_UNPROFILED_NOTE}</Alert>
            )}
            {result.records.map((record, index) => (
              <RecordPanel
                key={`${result.extraction_result_id}-${index}`}
                record={record}
                index={index + 1}
                placementTag={tag}
              />
            ))}
          </Stack>
        )
      })}
    </Stack>
  )
}

/**
 * Read-only viewer of the records a finished run's review set aside.
 * Nothing is decided, edited or saved here.
 */
export default function SetAsideRecordsDialog({
  open,
  onClose,
  flowName,
  papers,
}: SetAsideRecordsDialogProps) {
  const titleId = React.useId()
  const [loads, setLoads] = React.useState<Record<string, PaperLoad>>({})
  const controllerRef = React.useRef<AbortController | null>(null)
  const papersKey = papers.map(paperKey).join('|')

  const loadPaper = React.useCallback((paper: SetAsidePaper, signal: AbortSignal) => {
    const key = paperKey(paper)
    setLoads((current) => ({ ...current, [key]: { state: 'loading' } }))
    fetchFlowRunSetAsideRecords(paper.flowRunId, paper.documentId, { signal })
      .then((data) => {
        if (signal.aborted) return
        setLoads((current) => ({ ...current, [key]: { state: 'loaded', data } }))
      })
      .catch((error: unknown) => {
        if (signal.aborted) return
        setLoads((current) => ({
          ...current,
          [key]: { state: 'error', message: error instanceof Error ? error.message : String(error) },
        }))
      })
  }, [])

  React.useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    controllerRef.current = controller
    papers.forEach((paper) => loadPaper(paper, controller.signal))
    return () => controller.abort()
    // papersKey identifies the papers; a new array with the same papers does not refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, papersKey, loadPaper])

  const totalCount = papers.reduce((sum, paper) => sum + paper.summary.count, 0)
  const title = papers.length > 1
    ? `${recordCountText(totalCount)} set aside in ${papers.length} papers`
    : `${recordCountText(totalCount)} set aside`
  const subtitle = papers.length === 1 && papers[0].title
    ? `${papers[0].title} · Flow: ${flowName}`
    : `Flow: ${flowName}`

  const placements = papers.map((paper) => setAsidePlacement(paper.summary))
  const loadedResults = papers.flatMap((paper) => {
    const load = loads[paperKey(paper)]
    return load?.state === 'loaded' ? load.data.results : []
  })
  const anyLoaded = papers.some((paper) => loads[paperKey(paper)]?.state === 'loaded')
  const anyCanInclude = loadedResults.some((result) => result.review_fields_available)
  const showAdvice = anyLoaded && placements.some(isLeftOutSomewhere) && anyCanInclude
  const showAlreadyInFile = placements.every((placement) => placement === 'included')

  const retry = (paper: SetAsidePaper) => {
    if (controllerRef.current) loadPaper(paper, controllerRef.current.signal)
  }

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="md" aria-labelledby={titleId}>
      <DialogTitle id={`${titleId}-header`} sx={{ pb: 1 }}>
        <Box sx={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 2 }}>
          <Box sx={{ minWidth: 0 }}>
            <Typography id={titleId} variant="h6" component="h2">
              {title}
            </Typography>
            <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block' }}>
              {subtitle}
            </Typography>
          </Box>
          <IconButton onClick={onClose} size="small" aria-label="Close set-aside records">
            <Close fontSize="small" />
          </IconButton>
        </Box>
      </DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2}>
          {showAdvice && (
            <Alert severity="info">
              <Typography variant="body2" sx={{ fontWeight: 700 }}>
                {SET_ASIDE_INCLUDE_ADVICE}
              </Typography>
            </Alert>
          )}
          {showAlreadyInFile && <Alert severity="success">{SET_ASIDE_ALREADY_IN_FILE}</Alert>}

          {papers.length === 1 ? (
            <PaperRecords
              load={loads[paperKey(papers[0])] ?? { state: 'loading' }}
              placement={placements[0]}
              onRetry={() => retry(papers[0])}
            />
          ) : (
            <Box>
              {papers.map((paper, index) => (
                <Accordion
                  key={paperKey(paper)}
                  defaultExpanded={index === 0}
                  disableGutters
                  variant="outlined"
                >
                  <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                    <Stack
                      direction="row"
                      spacing={1}
                      sx={{ alignItems: 'center', justifyContent: 'space-between', width: '100%', pr: 1 }}
                    >
                      <Typography variant="subtitle2" component="span" sx={{ minWidth: 0 }}>
                        {paper.title}
                      </Typography>
                      <Chip
                        size="small"
                        variant="outlined"
                        color="warning"
                        label={`${paper.summary.count} set aside`}
                      />
                    </Stack>
                  </AccordionSummary>
                  <AccordionDetails>
                    <PaperRecords
                      load={loads[paperKey(paper)] ?? { state: 'loading' }}
                      placement={placements[index]}
                      onRetry={() => retry(paper)}
                    />
                  </AccordionDetails>
                </Accordion>
              ))}
            </Box>
          )}
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2, justifyContent: 'space-between' }}>
        <Typography variant="caption" sx={{ color: 'text.secondary' }}>
          {SET_ASIDE_READ_ONLY_NOTE}
        </Typography>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  )
}
