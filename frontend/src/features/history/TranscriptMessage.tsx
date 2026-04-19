import InsertDriveFileIcon from '@mui/icons-material/InsertDriveFile'
import { Box, Card, CardContent, Collapse, Typography } from '@mui/material'
import { useState } from 'react'

import { buildEvidenceLocationLabel } from '@/features/curation/evidence/navigationPresentation'
import type { EvidenceRecord } from '@/features/curation/types'
import type { FileInfo } from '@/components/Chat/FileDownloadCard'
import type { FlowStepEvidenceDetails } from '@/types/AuditEvent'

export type TranscriptMessageRole = 'user' | 'assistant' | 'flow'
type TranscriptFileFormat = 'csv' | 'tsv' | 'json'

export interface TranscriptMessageRecord {
  id?: string
  role: TranscriptMessageRole
  content: string
  timestamp?: string | Date | null
  type?: 'text' | 'file_download'
  fileData?: FileInfo | null
  flowStepEvidence?: FlowStepEvidenceDetails | null
  evidenceRecords?: EvidenceRecord[] | null
}

interface TranscriptMessageProps {
  message: TranscriptMessageRecord
}

interface TranscriptEvidenceChipItem {
  entity: string
  quoteCount: number
  colorHex: string
  chipBackground: string
  chipBorder: string
  activeBackground: string
  inactiveBackground: string
  inactiveBorder: string
}

interface TranscriptEvidenceQuoteGroup {
  entity: string
  evidenceRecords: EvidenceRecord[]
  colorHex: string
}

interface TranscriptEvidenceCardProps {
  evidenceRecords: EvidenceRecord[]
  headerLabel?: string
}

interface TranscriptFlowStepEvidenceCardProps {
  details: FlowStepEvidenceDetails
}

interface TranscriptFileCardProps {
  file: FileInfo
}

const EVIDENCE_COLOR_PALETTE = [
  {
    colorHex: '#64b5f6',
    chipBackground: 'rgba(100, 181, 246, 0.2)',
    chipBorder: 'rgba(100, 181, 246, 0.4)',
    activeBackground: 'rgba(100, 181, 246, 0.45)',
    inactiveBackground: 'rgba(100, 181, 246, 0.15)',
    inactiveBorder: 'rgba(100, 181, 246, 0.3)',
  },
  {
    colorHex: '#81c784',
    chipBackground: 'rgba(129, 199, 132, 0.2)',
    chipBorder: 'rgba(129, 199, 132, 0.4)',
    activeBackground: 'rgba(129, 199, 132, 0.45)',
    inactiveBackground: 'rgba(129, 199, 132, 0.15)',
    inactiveBorder: 'rgba(129, 199, 132, 0.3)',
  },
  {
    colorHex: '#ffb74d',
    chipBackground: 'rgba(255, 183, 77, 0.2)',
    chipBorder: 'rgba(255, 183, 77, 0.4)',
    activeBackground: 'rgba(255, 183, 77, 0.45)',
    inactiveBackground: 'rgba(255, 183, 77, 0.15)',
    inactiveBorder: 'rgba(255, 183, 77, 0.3)',
  },
  {
    colorHex: '#ce93d8',
    chipBackground: 'rgba(206, 147, 216, 0.2)',
    chipBorder: 'rgba(206, 147, 216, 0.4)',
    activeBackground: 'rgba(206, 147, 216, 0.45)',
    inactiveBackground: 'rgba(206, 147, 216, 0.15)',
    inactiveBorder: 'rgba(206, 147, 216, 0.3)',
  },
  {
    colorHex: '#ef9a9a',
    chipBackground: 'rgba(239, 154, 154, 0.2)',
    chipBorder: 'rgba(239, 154, 154, 0.4)',
    activeBackground: 'rgba(239, 154, 154, 0.45)',
    inactiveBackground: 'rgba(239, 154, 154, 0.15)',
    inactiveBorder: 'rgba(239, 154, 154, 0.3)',
  },
  {
    colorHex: '#80cbc4',
    chipBackground: 'rgba(128, 203, 196, 0.2)',
    chipBorder: 'rgba(128, 203, 196, 0.4)',
    activeBackground: 'rgba(128, 203, 196, 0.45)',
    inactiveBackground: 'rgba(128, 203, 196, 0.15)',
    inactiveBorder: 'rgba(128, 203, 196, 0.3)',
  },
  {
    colorHex: '#f48fb1',
    chipBackground: 'rgba(244, 143, 177, 0.2)',
    chipBorder: 'rgba(244, 143, 177, 0.4)',
    activeBackground: 'rgba(244, 143, 177, 0.45)',
    inactiveBackground: 'rgba(244, 143, 177, 0.15)',
    inactiveBorder: 'rgba(244, 143, 177, 0.3)',
  },
  {
    colorHex: '#aed581',
    chipBackground: 'rgba(174, 213, 129, 0.2)',
    chipBorder: 'rgba(174, 213, 129, 0.4)',
    activeBackground: 'rgba(174, 213, 129, 0.45)',
    inactiveBackground: 'rgba(174, 213, 129, 0.15)',
    inactiveBorder: 'rgba(174, 213, 129, 0.3)',
  },
] as const

function assertUnreachable(value: never, context: string): never {
  throw new Error(`Unhandled ${context}: ${String(value)}`)
}

function parseTranscriptFileFormat(format: string): TranscriptFileFormat {
  switch (format.toLowerCase()) {
    case 'csv':
      return 'csv'
    case 'tsv':
      return 'tsv'
    case 'json':
      return 'json'
    default:
      throw new Error(`Unsupported transcript file format: ${format}`)
  }
}

function formatEvidenceQuoteCount(count: number): string {
  return count === 1 ? '1 evidence quote' : `${count} evidence quotes`
}

function formatEvidencePreviewCount(count: number): string {
  return count === 1 ? '1 evidence quote preview' : `${count} evidence quote previews`
}

function formatStepEvidenceSummary(evidenceCount: number, previewCount: number): string {
  if (previewCount > 0 && previewCount < evidenceCount) {
    return `Showing ${formatEvidencePreviewCount(previewCount)} from ${formatEvidenceQuoteCount(evidenceCount)} captured in this step.`
  }

  return `${formatEvidenceQuoteCount(evidenceCount)} captured in this step.`
}

function formatFileSize(bytes?: number): string | null {
  if (bytes == null) {
    return null
  }

  if (bytes < 1024) {
    return `${bytes} B`
  }

  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`
  }

  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function getFormatLabel(format: TranscriptFileFormat): string {
  switch (format) {
    case 'csv':
      return 'CSV'
    case 'tsv':
      return 'TSV'
    case 'json':
      return 'JSON'
  }

  return assertUnreachable(format, 'transcript file format')
}

function getFormatColor(format: TranscriptFileFormat): string {
  switch (format) {
    case 'csv':
      return '#4caf50'
    case 'tsv':
      return '#2196f3'
    case 'json':
      return '#ff9800'
  }

  return assertUnreachable(format, 'transcript file format')
}

function buildEntityData(
  evidenceRecords: EvidenceRecord[],
): {
  chipItems: TranscriptEvidenceChipItem[]
  quoteGroups: TranscriptEvidenceQuoteGroup[]
} {
  const groupedRecords = new Map<string, EvidenceRecord[]>()

  evidenceRecords.forEach((record) => {
    const entityKey = record.entity.trim()
    if (!groupedRecords.has(entityKey)) {
      groupedRecords.set(entityKey, [])
    }

    groupedRecords.get(entityKey)?.push(record)
  })

  const chipItems: TranscriptEvidenceChipItem[] = []
  const quoteGroups: TranscriptEvidenceQuoteGroup[] = []

  Array.from(groupedRecords.entries()).forEach(([entity, records], index) => {
    const palette = EVIDENCE_COLOR_PALETTE[index % EVIDENCE_COLOR_PALETTE.length]

    chipItems.push({
      entity,
      quoteCount: records.length,
      ...palette,
    })

    quoteGroups.push({
      entity,
      evidenceRecords: records,
      colorHex: palette.colorHex,
    })
  })

  return { chipItems, quoteGroups }
}

function TranscriptEvidenceQuote({
  evidenceRecord,
  borderColor,
}: {
  evidenceRecord: EvidenceRecord
  borderColor: string
}) {
  const locationLabel = buildEvidenceLocationLabel({
    pageNumber: evidenceRecord.page,
    sectionTitle: evidenceRecord.section,
    subsectionTitle: evidenceRecord.subsection ?? null,
  })

  return (
    <Box
      data-testid="transcript-evidence-quote"
      sx={{
        backgroundColor: 'rgba(255, 255, 255, 0.08)',
        borderRadius: '8px',
        borderLeft: `3px solid ${borderColor}`,
        px: '12px',
        py: '10px',
      }}
    >
      <Box
        sx={{
          fontSize: '11px',
          color: 'rgba(255, 255, 255, 0.6)',
          mb: '4px',
        }}
      >
        {locationLabel}
      </Box>

      <Box
        sx={{
          fontSize: '13px',
          fontStyle: 'italic',
          lineHeight: 1.5,
          color: 'rgba(255, 255, 255, 0.9)',
        }}
      >
        &ldquo;{evidenceRecord.verified_quote}&rdquo;
      </Box>
    </Box>
  )
}

function TranscriptEvidenceCard({
  evidenceRecords,
  headerLabel,
}: TranscriptEvidenceCardProps) {
  const [activeEntity, setActiveEntity] = useState<string | null>(null)
  const { chipItems, quoteGroups } = buildEntityData(evidenceRecords)
  const activeGroup = activeEntity
    ? quoteGroups.find((group) => group.entity === activeEntity) ?? null
    : null
  const hasActiveEntity = activeEntity !== null

  return (
    <Box
      data-testid="transcript-evidence-card"
      sx={{
        backgroundColor: '#0d47a1',
        borderRadius: '0 0 18px 4px',
        borderTop: '1px solid rgba(255, 255, 255, 0.1)',
        px: '1rem',
        py: '10px',
        maxWidth: '100%',
        boxSizing: 'border-box',
      }}
    >
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          px: '4px',
          mb: '8px',
        }}
      >
        <Box
          aria-hidden="true"
          component="svg"
          data-testid="transcript-evidence-card-header-icon"
          fill="none"
          sx={{
            flexShrink: 0,
            width: '14px',
            height: '14px',
            display: 'block',
          }}
          viewBox="0 0 24 24"
        >
          <path
            d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"
            stroke="rgba(255,255,255,0.6)"
            strokeWidth="2"
          />
          <polyline
            points="14 2 14 8 20 8"
            stroke="rgba(255,255,255,0.6)"
            strokeWidth="2"
          />
          <line
            x1="16"
            x2="8"
            y1="13"
            y2="13"
            stroke="rgba(255,255,255,0.6)"
            strokeWidth="2"
          />
          <line
            x1="16"
            x2="8"
            y1="17"
            y2="17"
            stroke="rgba(255,255,255,0.6)"
            strokeWidth="2"
          />
        </Box>

        <Box
          sx={{
            fontSize: '12px',
            color: 'rgba(255, 255, 255, 0.7)',
          }}
        >
          {headerLabel ?? `${evidenceRecords.length} evidence quotes`}
        </Box>
      </Box>

      <Box
        sx={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: '6px',
        }}
      >
        {chipItems.map((item) => {
          const isActive = activeEntity === item.entity
          const backgroundColor = isActive
            ? item.activeBackground
            : hasActiveEntity
              ? item.inactiveBackground
              : item.chipBackground
          const border = isActive
            ? `2px solid ${item.colorHex}`
            : `1px solid ${hasActiveEntity ? item.inactiveBorder : item.chipBorder}`

          return (
            <Box
              aria-label={`${item.entity} ${item.quoteCount}`}
              aria-pressed={isActive}
              component="button"
              key={item.entity}
              onClick={() => {
                setActiveEntity((currentEntity) => (currentEntity === item.entity ? null : item.entity))
              }}
              sx={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '4px',
                borderRadius: '14px',
                px: isActive ? '11px' : '12px',
                py: isActive ? '3px' : '4px',
                border,
                backgroundColor,
                color: '#ffffff',
                fontSize: '12px',
                fontWeight: isActive ? 600 : 400,
                lineHeight: 1.2,
                cursor: 'pointer',
                opacity: hasActiveEntity && !isActive ? 0.6 : 1,
                transition: 'background-color 150ms ease, border-color 150ms ease, opacity 150ms ease',
              }}
              type="button"
            >
              <Box component="span">{item.entity}</Box>
              <Box
                component="span"
                sx={{
                  opacity: isActive ? 0.8 : 0.6,
                }}
              >
                {item.quoteCount}
              </Box>
            </Box>
          )
        })}
      </Box>

      <Collapse in={Boolean(activeGroup)} timeout="auto" unmountOnExit>
        {activeGroup ? (
          <Box
            sx={{
              display: 'flex',
              flexDirection: 'column',
              gap: '6px',
              mt: '10px',
              pb: '4px',
            }}
          >
            {activeGroup.evidenceRecords.map((record, index) => (
              <TranscriptEvidenceQuote
                borderColor={activeGroup.colorHex}
                evidenceRecord={record}
                key={`${activeGroup.entity}-${record.chunk_id}-${index}`}
              />
            ))}
          </Box>
        ) : null}
      </Collapse>
    </Box>
  )
}

function TranscriptFlowStepEvidenceCard({
  details,
}: TranscriptFlowStepEvidenceCardProps) {
  const evidenceRecords = details.evidence_records
  const previewCount = evidenceRecords.length
  const isPreviewSubset = previewCount > 0 && previewCount < details.evidence_count
  const agentLabel = details.agent_name?.trim() || null
  const toolLabel = details.tool_name?.trim() || null
  const sourceParts = [`Step ${details.step}`]

  if (agentLabel) {
    sourceParts.push(agentLabel)
  }

  if (toolLabel) {
    sourceParts.push(toolLabel)
  }

  return (
    <Box
      data-testid="transcript-flow-step-evidence-card"
      sx={{
        alignSelf: 'flex-start',
        display: 'flex',
        flexDirection: 'column',
        maxWidth: '85%',
        minWidth: 0,
      }}
    >
      <Box
        sx={{
          backgroundColor: 'rgba(255, 255, 255, 0.08)',
          borderRadius: '18px 18px 4px 4px',
          color: '#ffffff',
          px: '1rem',
          py: '0.85rem',
        }}
      >
        <Box
          sx={{
            fontSize: '11px',
            fontWeight: 600,
            letterSpacing: '0.04em',
            opacity: 0.72,
            textTransform: 'uppercase',
          }}
        >
          Flow evidence
        </Box>

        <Box
          sx={{
            fontSize: '15px',
            fontWeight: 600,
            mt: '4px',
          }}
        >
          {sourceParts.join(' / ')}
        </Box>

        <Box
          sx={{
            fontSize: '13px',
            lineHeight: 1.45,
            mt: '6px',
            opacity: 0.9,
          }}
        >
          {formatStepEvidenceSummary(details.evidence_count, previewCount)}
        </Box>

        <Box
          sx={{
            fontSize: '12px',
            mt: '4px',
            opacity: 0.66,
          }}
        >
          {formatEvidenceQuoteCount(details.total_evidence_records)} collected so far in this run.
        </Box>
      </Box>

      {evidenceRecords.length > 0 ? (
        <TranscriptEvidenceCard
          evidenceRecords={evidenceRecords}
          headerLabel={
            isPreviewSubset
              ? formatEvidencePreviewCount(previewCount)
              : formatEvidenceQuoteCount(previewCount)
          }
        />
      ) : (
        <Box
          data-testid="transcript-flow-step-evidence-empty"
          sx={{
            backgroundColor: '#0d47a1',
            borderRadius: '0 0 18px 4px',
            borderTop: '1px solid rgba(255, 255, 255, 0.1)',
            color: 'rgba(255, 255, 255, 0.8)',
            fontSize: '12px',
            lineHeight: 1.45,
            px: '1rem',
            py: '10px',
          }}
        >
          No quote previews were attached to this step.
        </Box>
      )}
    </Box>
  )
}

function TranscriptFileCard({ file }: TranscriptFileCardProps) {
  const format = parseTranscriptFileFormat(file.format)
  const formatColor = getFormatColor(format)
  const fileSize = formatFileSize(file.size_bytes)

  return (
    <Card
      data-testid="transcript-file-card"
      sx={{
        mt: 1,
        mb: 1,
        backgroundColor: 'rgba(255, 255, 255, 0.05)',
        border: '1px solid rgba(255, 255, 255, 0.12)',
        borderRadius: 2,
        maxWidth: 400,
      }}
    >
      <CardContent sx={{ py: 1.5, px: 2, '&:last-child': { pb: 1.5 } }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 40,
              height: 40,
              borderRadius: 1,
              backgroundColor: `${formatColor}20`,
              flexShrink: 0,
            }}
          >
            <InsertDriveFileIcon sx={{ color: formatColor, fontSize: 24 }} />
          </Box>

          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography
              sx={{
                fontSize: '0.875rem',
                fontWeight: 500,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                color: 'rgba(255, 255, 255, 0.9)',
              }}
              title={file.filename}
              variant="body2"
            >
              {file.filename}
            </Typography>

            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 0.25 }}>
              <Typography
                sx={{
                  px: 0.75,
                  py: 0.125,
                  borderRadius: 0.5,
                  backgroundColor: `${formatColor}30`,
                  color: formatColor,
                  fontWeight: 600,
                  fontSize: '0.7rem',
                }}
                variant="caption"
              >
                {getFormatLabel(format)}
              </Typography>

              {fileSize ? (
                <Typography
                  sx={{ color: 'rgba(255, 255, 255, 0.5)' }}
                  variant="caption"
                >
                  {fileSize}
                </Typography>
              ) : null}
            </Box>
          </Box>
        </Box>
      </CardContent>
    </Card>
  )
}

function getRoleLabel(role: TranscriptMessageRole): string {
  switch (role) {
    case 'assistant':
      return 'AI Assistant'
    case 'flow':
      return 'Flow'
    case 'user':
      return 'You'
  }

  return assertUnreachable(role, 'transcript message role')
}

function getBubbleStyles(
  role: TranscriptMessageRole,
  hasEvidenceCard: boolean,
): {
  alignSelf: 'flex-start' | 'flex-end'
  maxWidth: string
  backgroundColor: string
  borderRadius: string
} {
  switch (role) {
    case 'user':
      return {
        alignSelf: 'flex-end',
        maxWidth: '75%',
        backgroundColor: '#424242',
        borderRadius: '18px 18px 4px 18px',
      }
    case 'flow':
      return {
        alignSelf: 'flex-start',
        maxWidth: '85%',
        backgroundColor: 'rgba(255, 255, 255, 0.08)',
        borderRadius: '18px 18px 18px 4px',
      }
    case 'assistant':
      return {
        alignSelf: 'flex-start',
        maxWidth: '85%',
        backgroundColor: '#1565c0',
        borderRadius: hasEvidenceCard ? '18px 18px 4px 4px' : '18px 18px 18px 4px',
      }
  }

  return assertUnreachable(role, 'transcript message role')
}

export default function TranscriptMessage({ message }: TranscriptMessageProps) {
  if (message.role === 'flow' && message.flowStepEvidence) {
    return <TranscriptFlowStepEvidenceCard details={message.flowStepEvidence} />
  }

  const hasEvidenceCard = (message.evidenceRecords?.length ?? 0) > 0
  const bubbleStyles = getBubbleStyles(message.role, hasEvidenceCard)

  return (
    <Box
      data-testid={`transcript-message-${message.role}`}
      sx={{
        alignSelf: bubbleStyles.alignSelf,
        maxWidth: bubbleStyles.maxWidth,
        display: 'flex',
        flexDirection: 'column',
        minWidth: 0,
      }}
    >
      <Box
        sx={{
          backgroundColor: bubbleStyles.backgroundColor,
          color: '#ffffff',
          padding: '1rem 1.5rem',
          borderRadius: bubbleStyles.borderRadius,
          boxShadow: '0 1px 3px rgba(0,0,0,0.12)',
        }}
      >
        <Box
          sx={{
            fontSize: '0.75rem',
            fontWeight: 600,
            opacity: 0.8,
            mb: '0.5rem',
            textTransform: 'uppercase',
            letterSpacing: '0.5px',
          }}
        >
          {getRoleLabel(message.role)}
        </Box>

        <Box
          sx={{
            lineHeight: 1.5,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {message.type === 'file_download' && message.fileData ? (
            <TranscriptFileCard file={message.fileData} />
          ) : (
            message.content
          )}
        </Box>
      </Box>

      {message.role === 'assistant' && hasEvidenceCard ? (
        <TranscriptEvidenceCard evidenceRecords={message.evidenceRecords ?? []} />
      ) : null}
    </Box>
  )
}
