import { Box } from '@mui/material'

import { dispatchPDFViewerNavigateEvidence } from '@/components/pdfViewer/pdfEvents'
import type { EvidenceRecord } from '@/features/curation/types'

import { buildChatEvidenceNavigationCommand } from './chatEvidenceNavigation'

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

export default function EvidenceQuote({
  evidenceRecord,
  borderColor,
}: EvidenceQuoteProps) {
  return (
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
        {buildMetadataLabel(evidenceRecord)}
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
  )
}
