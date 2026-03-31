import ContentCopyIcon from '@mui/icons-material/ContentCopy'
import { Box, IconButton, Tooltip } from '@mui/material'

import { dispatchPDFViewerNavigateEvidence } from '@/components/pdfViewer/pdfEvents'
import type { EvidenceRecord } from '@/features/curation/types'

import { buildChatEvidenceNavigationCommand } from './chatEvidenceNavigation'
import { copyText } from './copyText'
interface EvidenceQuoteProps {
  evidenceRecord: EvidenceRecord
  borderColor: string
}

function buildMetadataLabel(evidenceRecord: EvidenceRecord): string {
  const sectionLabel = evidenceRecord.subsection
    ? `${evidenceRecord.section} › ${evidenceRecord.subsection}`
    : evidenceRecord.section

  return `p. ${evidenceRecord.page} · ${sectionLabel}`
}

function buildEvidenceQuoteCopyText(evidenceRecord: EvidenceRecord): string {
  return `${buildMetadataLabel(evidenceRecord)}\n"${evidenceRecord.verified_quote.trim()}"`
}

export default function EvidenceQuote({
  evidenceRecord,
  borderColor,
}: EvidenceQuoteProps) {
  const metadataLabel = buildMetadataLabel(evidenceRecord)

  return (
    <Box
      sx={{
        position: 'relative',
      }}
    >
      <Box
        aria-label={`Highlight evidence on PDF: ${evidenceRecord.verified_quote}`}
        component="button"
        data-testid={`evidence-quote-${evidenceRecord.chunk_id}`}
        onClick={() => {
          const command = buildChatEvidenceNavigationCommand(evidenceRecord)
          if (window.__pdfViewerEvidenceDebug?.enabled) {
            console.info('[PDF EVIDENCE DEBUG] Dispatching chat evidence quote navigation', {
              anchorId: command.anchorId,
              chunkId: evidenceRecord.chunk_id,
              entity: evidenceRecord.entity,
              page: evidenceRecord.page,
              section: evidenceRecord.section,
              subsection: evidenceRecord.subsection ?? null,
              quote: evidenceRecord.verified_quote,
            })
          }
          dispatchPDFViewerNavigateEvidence(command)
        }}
        sx={{
          backgroundColor: 'rgba(255, 255, 255, 0.08)',
          borderRadius: '8px',
          border: 0,
          px: '12px',
          py: '10px',
          pr: '44px',
          pb: '34px',
          borderLeft: `3px solid ${borderColor}`,
          cursor: 'pointer',
          display: 'block',
          font: 'inherit',
          textAlign: 'left',
          width: '100%',
          transition: 'background-color 140ms ease, transform 140ms ease',
          '&:hover': {
            backgroundColor: 'rgba(255, 255, 255, 0.12)',
            transform: 'translateX(2px)',
          },
          '&:focus-visible': {
            outline: `2px solid ${borderColor}`,
            outlineOffset: '2px',
          },
        }}
        type="button"
      >
        <Box
          sx={{
            fontSize: '11px',
            color: 'rgba(255, 255, 255, 0.6)',
            mb: '4px',
          }}
        >
          {metadataLabel}
        </Box>

        <Box
          sx={{
            fontSize: '13px',
            fontStyle: 'italic',
            lineHeight: 1.4,
            color: 'rgba(255, 255, 255, 0.9)',
          }}
        >
          "{evidenceRecord.verified_quote}"
        </Box>

        <Box
          sx={{
            fontSize: '11px',
            color: 'rgba(255, 255, 255, 0.56)',
            mt: '6px',
          }}
        >
          Click to highlight this passage in the PDF
        </Box>
      </Box>

      <Tooltip title="Copy evidence quote">
        <IconButton
          aria-label={`Copy evidence quote: ${evidenceRecord.verified_quote}`}
          data-testid={`copy-evidence-quote-${evidenceRecord.chunk_id}`}
          onClick={(event) => {
            event.preventDefault()
            event.stopPropagation()
            copyText(buildEvidenceQuoteCopyText(evidenceRecord)).catch((error) => {
              console.error('Failed to copy evidence quote:', error)
            })
          }}
          size="small"
          sx={{
            position: 'absolute',
            right: '8px',
            bottom: '8px',
            backgroundColor: 'rgba(255, 255, 255, 0.08)',
            border: '1px solid rgba(255, 255, 255, 0.12)',
            color: 'rgba(255, 255, 255, 0.68)',
            '&:hover': {
              backgroundColor: 'rgba(255, 255, 255, 0.16)',
              color: '#ffffff',
            },
          }}
          type="button"
        >
          <ContentCopyIcon fontSize="inherit" />
        </IconButton>
      </Tooltip>
    </Box>
  )
}
