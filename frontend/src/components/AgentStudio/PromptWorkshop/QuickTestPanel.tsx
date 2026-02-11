import { useEffect, useMemo, useState } from 'react'
import { alpha } from '@mui/material/styles'
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  FormControlLabel,
  Grid,
  Paper,
  Stack,
  Switch,
  TextField,
  Typography,
} from '@mui/material'
import type { CustomAgent, CustomAgentTestEvent, PromptInfo } from '@/types/promptExplorer'
import {
  fetchActiveChatDocument,
  streamAgentTest,
  streamCustomAgentTest,
} from '@/services/agentStudioService'

interface QuickTestPanelProps {
  customAgent?: CustomAgent
  parentAgent?: PromptInfo
  modId?: string
}

type RunMode = 'single' | 'compare' | null
type CompareView = 'semantic_diff' | 'raw'
type SentenceStatus = 'unchanged' | 'changed' | 'added' | 'removed'
type SegmentType = 'same' | 'added' | 'removed'

interface StreamResult {
  text: string
  error: string | null
}

interface WordSegment {
  text: string
  type: SegmentType
}

interface SemanticDiffRow {
  status: SentenceStatus
  similarity: number
  customSentence: string
  originalSentence: string
  customSegments?: WordSegment[]
  originalSegments?: WordSegment[]
}

const GAP_PENALTY = -0.45
const MIN_SENTENCE_MATCH = 0.18

function normalizeWhitespace(value: string): string {
  return value.replace(/\s+/g, ' ').trim()
}

function splitWords(value: string): string[] {
  const trimmed = value.trim()
  if (!trimmed) return []
  return trimmed.split(/\s+/)
}

function normalizeToken(token: string): string {
  return token
    .toLowerCase()
    .replace(/^[^a-z0-9]+/g, '')
    .replace(/[^a-z0-9]+$/g, '')
}

function splitIntoSentences(text: string): string[] {
  const normalized = text.replace(/\r/g, '\n').trim()
  if (!normalized) return []

  const chunks = normalized.split(/\n+/)
  const sentences: string[] = []

  for (const chunk of chunks) {
    const trimmed = chunk.trim()
    if (!trimmed) continue

    const parts = trimmed.split(/(?<=[.!?])\s+/)
    for (const part of parts) {
      const sentence = part.trim()
      if (sentence) {
        sentences.push(sentence)
      }
    }
  }

  return sentences
}

function sentenceSimilarity(customSentence: string, originalSentence: string): number {
  const customNorm = normalizeWhitespace(customSentence)
  const originalNorm = normalizeWhitespace(originalSentence)
  if (!customNorm && !originalNorm) return 1
  if (!customNorm || !originalNorm) return 0
  if (customNorm === originalNorm) return 1

  const customWords = splitWords(customNorm).map(normalizeToken).filter(Boolean)
  const originalWords = splitWords(originalNorm).map(normalizeToken).filter(Boolean)
  if (customWords.length === 0 || originalWords.length === 0) return 0

  const customCounts = new Map<string, number>()
  const originalCounts = new Map<string, number>()

  for (const word of customWords) {
    customCounts.set(word, (customCounts.get(word) || 0) + 1)
  }
  for (const word of originalWords) {
    originalCounts.set(word, (originalCounts.get(word) || 0) + 1)
  }

  let overlap = 0
  for (const [word, customCount] of customCounts.entries()) {
    const originalCount = originalCounts.get(word) || 0
    overlap += Math.min(customCount, originalCount)
  }

  return (2 * overlap) / (customWords.length + originalWords.length)
}

function computeWordDiff(customSentence: string, originalSentence: string): {
  customSegments: WordSegment[]
  originalSegments: WordSegment[]
} {
  const originalWords = splitWords(originalSentence)
  const customWords = splitWords(customSentence)

  const rows = originalWords.length + 1
  const cols = customWords.length + 1
  const lcs: number[][] = Array.from({ length: rows }, () => Array(cols).fill(0))

  for (let i = 1; i < rows; i += 1) {
    for (let j = 1; j < cols; j += 1) {
      if (normalizeToken(originalWords[i - 1]) === normalizeToken(customWords[j - 1])) {
        lcs[i][j] = lcs[i - 1][j - 1] + 1
      } else {
        lcs[i][j] = Math.max(lcs[i - 1][j], lcs[i][j - 1])
      }
    }
  }

  const customSegments: WordSegment[] = []
  const originalSegments: WordSegment[] = []
  let i = originalWords.length
  let j = customWords.length

  while (i > 0 || j > 0) {
    if (
      i > 0 &&
      j > 0 &&
      normalizeToken(originalWords[i - 1]) === normalizeToken(customWords[j - 1])
    ) {
      customSegments.push({ text: customWords[j - 1], type: 'same' })
      originalSegments.push({ text: originalWords[i - 1], type: 'same' })
      i -= 1
      j -= 1
      continue
    }

    if (j > 0 && (i === 0 || lcs[i][j - 1] >= lcs[i - 1][j])) {
      customSegments.push({ text: customWords[j - 1], type: 'added' })
      j -= 1
      continue
    }

    if (i > 0) {
      originalSegments.push({ text: originalWords[i - 1], type: 'removed' })
      i -= 1
    }
  }

  customSegments.reverse()
  originalSegments.reverse()

  return { customSegments, originalSegments }
}

function buildSemanticRows(customText: string, originalText: string): SemanticDiffRow[] {
  const customSentences = splitIntoSentences(customText)
  const originalSentences = splitIntoSentences(originalText)

  if (customSentences.length === 0 && originalSentences.length === 0) return []

  const rows = customSentences.length + 1
  const cols = originalSentences.length + 1
  const score: number[][] = Array.from({ length: rows }, () => Array(cols).fill(0))
  const backtrack: Array<Array<'diag' | 'up' | 'left' | null>> =
    Array.from({ length: rows }, () => Array(cols).fill(null))

  for (let i = 1; i < rows; i += 1) {
    score[i][0] = score[i - 1][0] + GAP_PENALTY
    backtrack[i][0] = 'up'
  }
  for (let j = 1; j < cols; j += 1) {
    score[0][j] = score[0][j - 1] + GAP_PENALTY
    backtrack[0][j] = 'left'
  }

  for (let i = 1; i < rows; i += 1) {
    for (let j = 1; j < cols; j += 1) {
      const similarity = sentenceSimilarity(customSentences[i - 1], originalSentences[j - 1])
      const matchBonus = similarity >= MIN_SENTENCE_MATCH ? similarity * 2 : -0.35
      const diag = score[i - 1][j - 1] + matchBonus
      const up = score[i - 1][j] + GAP_PENALTY
      const left = score[i][j - 1] + GAP_PENALTY

      if (diag >= up && diag >= left) {
        score[i][j] = diag
        backtrack[i][j] = 'diag'
      } else if (up >= left) {
        score[i][j] = up
        backtrack[i][j] = 'up'
      } else {
        score[i][j] = left
        backtrack[i][j] = 'left'
      }
    }
  }

  const aligned: SemanticDiffRow[] = []
  let i = customSentences.length
  let j = originalSentences.length

  while (i > 0 || j > 0) {
    const direction = backtrack[i][j]

    if (direction === 'diag' && i > 0 && j > 0) {
      const customSentence = customSentences[i - 1]
      const originalSentence = originalSentences[j - 1]
      const similarity = sentenceSimilarity(customSentence, originalSentence)
      const same = normalizeWhitespace(customSentence) === normalizeWhitespace(originalSentence)
      const row: SemanticDiffRow = {
        status: same ? 'unchanged' : 'changed',
        similarity,
        customSentence,
        originalSentence,
      }

      if (!same) {
        const { customSegments, originalSegments } = computeWordDiff(customSentence, originalSentence)
        row.customSegments = customSegments
        row.originalSegments = originalSegments
      }

      aligned.push(row)
      i -= 1
      j -= 1
      continue
    }

    if ((direction === 'up' || j === 0) && i > 0) {
      const customSentence = customSentences[i - 1]
      aligned.push({
        status: 'added',
        similarity: 0,
        customSentence,
        originalSentence: '',
        customSegments: splitWords(customSentence).map((word) => ({ text: word, type: 'added' })),
      })
      i -= 1
      continue
    }

    if (j > 0) {
      const originalSentence = originalSentences[j - 1]
      aligned.push({
        status: 'removed',
        similarity: 0,
        customSentence: '',
        originalSentence,
        originalSegments: splitWords(originalSentence).map((word) => ({ text: word, type: 'removed' })),
      })
      j -= 1
    }
  }

  aligned.reverse()
  return aligned
}

function QuickTestPanel({ customAgent, parentAgent, modId }: QuickTestPanelProps) {
  const [input, setInput] = useState('')
  const [documentId, setDocumentId] = useState('')
  const [activeDocument, setActiveDocument] = useState<{ id: string; filename?: string } | null>(null)
  const [activeDocumentLoading, setActiveDocumentLoading] = useState(false)

  const [runMode, setRunMode] = useState<RunMode>(null)
  const [showComparison, setShowComparison] = useState(false)
  const [compareView, setCompareView] = useState<CompareView>('semantic_diff')
  const [hideUnchanged, setHideUnchanged] = useState(true)

  const [customOutput, setCustomOutput] = useState('')
  const [originalOutput, setOriginalOutput] = useState('')
  const [customError, setCustomError] = useState<string | null>(null)
  const [originalError, setOriginalError] = useState<string | null>(null)
  const [validationError, setValidationError] = useState<string | null>(null)

  useEffect(() => {
    async function loadActiveDocument() {
      setActiveDocumentLoading(true)
      try {
        const response = await fetchActiveChatDocument()
        if (response.active && response.document?.id) {
          setActiveDocument(response.document)
          setDocumentId((current) => current || response.document?.id || '')
        } else {
          setActiveDocument(null)
        }
      } catch {
        setActiveDocument(null)
      } finally {
        setActiveDocumentLoading(false)
      }
    }
    loadActiveDocument()
  }, [])

  const parseStream = async (
    stream: AsyncGenerator<Record<string, unknown>>,
    onDelta: (value: string) => void
  ): Promise<StreamResult> => {
    let text = ''

    try {
      for await (const rawEvent of stream) {
        const event = rawEvent as CustomAgentTestEvent

        if ((event.type === 'TEXT_MESSAGE_CONTENT' || event.type === 'TEXT_DELTA') && typeof event.delta === 'string') {
          text += event.delta
          onDelta(text)
          continue
        }

        if (event.type === 'RUN_FINISHED' && typeof event.response === 'string' && !text) {
          text = event.response
          onDelta(text)
          continue
        }

        if ((event.type === 'RUN_ERROR' || event.type === 'ERROR') && typeof event.message === 'string') {
          return { text, error: event.message }
        }

        if (event.type === 'DONE') {
          break
        }
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Test stream failed'
      return { text, error: message }
    }

    return { text, error: null }
  }

  const validateInput = (): boolean => {
    if (!customAgent) {
      setValidationError('Save the custom agent first, then run a quick test.')
      return false
    }
    if (!input.trim()) {
      setValidationError('Please enter a test input.')
      return false
    }
    setValidationError(null)
    return true
  }

  const buildRequest = () => ({
    input,
    mod_id: modId?.trim() || undefined,
    document_id: documentId.trim() || undefined,
  })

  const handleRunTest = async () => {
    if (!validateInput() || !customAgent) return

    setRunMode('single')
    setShowComparison(false)
    setCustomOutput('')
    setOriginalOutput('')
    setCustomError(null)
    setOriginalError(null)

    const result = await parseStream(
      streamCustomAgentTest(customAgent.id, buildRequest()),
      setCustomOutput
    )

    setCustomError(result.error)
    setRunMode(null)
  }

  const handleCompare = async () => {
    if (!validateInput() || !customAgent || !parentAgent) return

    setRunMode('compare')
    setShowComparison(true)
    setCustomOutput('')
    setOriginalOutput('')
    setCustomError(null)
    setOriginalError(null)

    const [customResult, originalResult] = await Promise.all([
      parseStream(streamCustomAgentTest(customAgent.id, buildRequest()), setCustomOutput),
      parseStream(streamAgentTest(parentAgent.agent_id, buildRequest()), setOriginalOutput),
    ])

    setCustomError(customResult.error)
    setOriginalError(originalResult.error)
    setRunMode(null)
  }

  const isRunning = runMode !== null
  const semanticRows = useMemo(
    () => buildSemanticRows(customOutput, originalOutput),
    [customOutput, originalOutput]
  )
  const filteredSemanticRows = useMemo(
    () => (hideUnchanged ? semanticRows.filter((row) => row.status !== 'unchanged') : semanticRows),
    [hideUnchanged, semanticRows]
  )
  const semanticStats = useMemo(
    () => semanticRows.reduce(
      (acc, row) => {
        if (row.status === 'changed') acc.changed += 1
        if (row.status === 'added') acc.added += 1
        if (row.status === 'removed') acc.removed += 1
        if (row.status === 'unchanged') acc.unchanged += 1
        return acc
      },
      { changed: 0, added: 0, removed: 0, unchanged: 0 }
    ),
    [semanticRows]
  )

  const renderSegments = (
    segments: WordSegment[] | undefined,
    fallbackText: string,
    emptyText: string
  ) => {
    if (segments && segments.length > 0) {
      return (
        <Typography
          variant="body2"
          sx={{
            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
            lineHeight: 1.5,
          }}
        >
          {segments.map((segment, index) => (
            <Box
              key={`${segment.text}-${index}`}
              component="span"
              sx={(theme) => ({
                display: 'inline',
                px: segment.type === 'same' ? 0 : 0.25,
                py: segment.type === 'same' ? 0 : 0.05,
                borderRadius: 0.5,
                backgroundColor:
                  segment.type === 'added'
                    ? alpha(theme.palette.success.main, 0.18)
                    : segment.type === 'removed'
                    ? alpha(theme.palette.error.main, 0.16)
                    : 'transparent',
                textDecoration: segment.type === 'removed' ? 'line-through' : 'none',
              })}
            >
              {segment.text}
              {index < segments.length - 1 ? ' ' : ''}
            </Box>
          ))}
        </Typography>
      )
    }

    if (fallbackText.trim()) {
      return (
        <Typography
          variant="body2"
          sx={{
            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
            lineHeight: 1.5,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {fallbackText}
        </Typography>
      )
    }

    return (
      <Typography variant="body2" color="text.secondary" fontStyle="italic">
        {emptyText}
      </Typography>
    )
  }

  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Typography variant="subtitle2" sx={{ mb: 1 }}>
        Quick Test
      </Typography>

      {validationError && (
        <Alert severity="error" sx={{ mb: 1 }}>
          {validationError}
        </Alert>
      )}

      <Stack spacing={1.5}>
        <TextField
          fullWidth
          label="Test Input"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          multiline
          minRows={2}
        />

        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
          <TextField
            size="small"
            label="Document ID (optional)"
            value={documentId}
            onChange={(event) => setDocumentId(event.target.value)}
            sx={{ minWidth: 280 }}
          />
          <Button
            size="small"
            variant="text"
            onClick={() => {
              if (activeDocument?.id) {
                setDocumentId(activeDocument.id)
              }
            }}
            disabled={!activeDocument?.id}
          >
            Use active chat document
          </Button>
          {activeDocumentLoading && <CircularProgress size={16} />}
        </Stack>

        {activeDocument && (
          <Typography variant="caption" color="text.secondary">
            Active chat document: {activeDocument.filename || activeDocument.id}
          </Typography>
        )}

        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
          <Button
            variant="contained"
            onClick={handleRunTest}
            disabled={isRunning || !customAgent}
          >
            Run Test
          </Button>
          <Button
            variant="outlined"
            onClick={handleCompare}
            disabled={isRunning || !customAgent || !parentAgent}
          >
            Compare with Original
          </Button>
          {isRunning && <CircularProgress size={20} />}
        </Stack>

        {!showComparison && (
          <>
            {customError && <Alert severity="error">{customError}</Alert>}
            <TextField
              fullWidth
              multiline
              minRows={8}
              value={customOutput}
              placeholder="Streaming test output appears here"
              InputProps={{ readOnly: true }}
              sx={{
                '& .MuiInputBase-root': {
                  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
                  fontSize: '0.8rem',
                },
              }}
            />
          </>
        )}

        {showComparison && (
          <Stack spacing={1.25}>
            <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
              <Button
                size="small"
                variant={compareView === 'semantic_diff' ? 'contained' : 'outlined'}
                onClick={() => setCompareView('semantic_diff')}
              >
                Semantic Diff
              </Button>
              <Button
                size="small"
                variant={compareView === 'raw' ? 'contained' : 'outlined'}
                onClick={() => setCompareView('raw')}
              >
                Raw Outputs
              </Button>

              <FormControlLabel
                sx={{ ml: 0.5 }}
                control={
                  <Switch
                    size="small"
                    checked={hideUnchanged}
                    onChange={(event) => setHideUnchanged(event.target.checked)}
                    disabled={compareView !== 'semantic_diff'}
                  />
                }
                label="Hide unchanged"
              />
            </Stack>

            <Stack direction="row" spacing={1} flexWrap="wrap">
              <Chip size="small" label={`Changed: ${semanticStats.changed}`} color="warning" variant="outlined" />
              <Chip size="small" label={`Added: ${semanticStats.added}`} color="success" variant="outlined" />
              <Chip size="small" label={`Removed: ${semanticStats.removed}`} color="error" variant="outlined" />
              <Chip size="small" label={`Unchanged: ${semanticStats.unchanged}`} variant="outlined" />
            </Stack>

            {customError && <Alert severity="error">{customError}</Alert>}
            {originalError && <Alert severity="error">{originalError}</Alert>}

            {compareView === 'raw' ? (
              <Grid container spacing={2}>
                <Grid item xs={12} md={6}>
                  <Typography variant="caption" color="text.secondary">
                    Custom Agent{customAgent ? `: ${customAgent.name}` : ''}
                  </Typography>
                  <TextField
                    fullWidth
                    multiline
                    minRows={10}
                    value={customOutput}
                    placeholder="Custom agent output"
                    InputProps={{ readOnly: true }}
                    sx={{
                      '& .MuiInputBase-root': {
                        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
                        fontSize: '0.8rem',
                      },
                    }}
                  />
                </Grid>
                <Grid item xs={12} md={6}>
                  <Typography variant="caption" color="text.secondary">
                    Original Agent{parentAgent ? `: ${parentAgent.agent_name}` : ''}
                  </Typography>
                  <TextField
                    fullWidth
                    multiline
                    minRows={10}
                    value={originalOutput}
                    placeholder="Original agent output"
                    InputProps={{ readOnly: true }}
                    sx={{
                      '& .MuiInputBase-root': {
                        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
                        fontSize: '0.8rem',
                      },
                    }}
                  />
                </Grid>
              </Grid>
            ) : (
              <Paper variant="outlined" sx={{ overflow: 'hidden' }}>
                <Box
                  sx={{
                    display: 'grid',
                    gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' },
                    borderBottom: '1px solid',
                    borderColor: 'divider',
                    bgcolor: 'action.hover',
                  }}
                >
                  <Box sx={{ p: 1, borderRight: { md: '1px solid' }, borderColor: 'divider' }}>
                    <Typography variant="caption" color="text.secondary">
                      Custom Agent{customAgent ? `: ${customAgent.name}` : ''}
                    </Typography>
                  </Box>
                  <Box sx={{ p: 1 }}>
                    <Typography variant="caption" color="text.secondary">
                      Original Agent{parentAgent ? `: ${parentAgent.agent_name}` : ''}
                    </Typography>
                  </Box>
                </Box>

                <Box sx={{ maxHeight: 420, overflow: 'auto' }}>
                  {filteredSemanticRows.length === 0 ? (
                    <Box sx={{ p: 2 }}>
                      <Typography variant="body2" color="text.secondary">
                        {semanticRows.length === 0
                          ? 'Run compare to generate a semantic diff.'
                          : 'No changed rows with current filters.'}
                      </Typography>
                    </Box>
                  ) : (
                    filteredSemanticRows.map((row, index) => (
                      <Box
                        key={`${row.status}-${index}`}
                        sx={(theme) => ({
                          display: 'grid',
                          gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' },
                          borderBottom: '1px solid',
                          borderColor: 'divider',
                          backgroundColor:
                            row.status === 'changed'
                              ? alpha(theme.palette.warning.main, 0.08)
                              : row.status === 'added'
                              ? alpha(theme.palette.success.main, 0.08)
                              : row.status === 'removed'
                              ? alpha(theme.palette.error.main, 0.08)
                              : 'transparent',
                        })}
                      >
                        <Box sx={{ p: 1.25, borderRight: { md: '1px solid' }, borderColor: 'divider' }}>
                          {renderSegments(
                            row.customSegments,
                            row.customSentence,
                            row.status === 'removed' ? 'No matching sentence in custom output' : 'No content'
                          )}
                        </Box>
                        <Box sx={{ p: 1.25 }}>
                          {renderSegments(
                            row.originalSegments,
                            row.originalSentence,
                            row.status === 'added' ? 'No matching sentence in original output' : 'No content'
                          )}
                        </Box>
                        {row.status === 'changed' && (
                          <Box
                            sx={{
                              gridColumn: '1 / -1',
                              px: 1.25,
                              pb: 1,
                            }}
                          >
                            <Typography variant="caption" color="text.secondary">
                              Similarity: {(row.similarity * 100).toFixed(0)}%
                            </Typography>
                          </Box>
                        )}
                      </Box>
                    ))
                  )}
                </Box>
              </Paper>
            )}
          </Stack>
        )}
      </Stack>
    </Paper>
  )
}

export default QuickTestPanel
