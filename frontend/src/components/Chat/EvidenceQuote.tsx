import { Box } from '@mui/material'

import EvidenceNavigationQuoteCard from '@/features/curation/evidence/EvidenceNavigationQuoteCard'
import {
  buildEvidenceLocationLabel,
} from '@/features/curation/evidence/navigationPresentation'
import type { EvidenceRecord } from '@/features/curation/types'

import { buildChatEvidenceNavigationCommand } from './chatEvidenceNavigation'
import { copyText } from './copyText'

interface EvidenceQuoteProps {
  evidenceRecord: EvidenceRecord
  borderColor: string
  interactive?: boolean
  testId?: string
}

function buildMetadataLabel(evidenceRecord: EvidenceRecord): string {
  return buildEvidenceLocationLabel({
    pageNumber: evidenceRecord.page,
    sectionTitle: evidenceRecord.section,
    subsectionTitle: evidenceRecord.subsection ?? null,
  })
}

function buildEvidenceQuoteCopyText(evidenceRecord: EvidenceRecord): string {
  return `${buildMetadataLabel(evidenceRecord)}\n"${evidenceRecord.verified_quote.trim()}"`
}

export default function EvidenceQuote({
  evidenceRecord,
  borderColor,
  interactive = true,
  testId,
}: EvidenceQuoteProps) {
  if (!interactive) {
    return (
      <Box
        data-testid={testId}
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
          {buildMetadataLabel(evidenceRecord)}
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

  const command = buildChatEvidenceNavigationCommand(evidenceRecord)

  return (
    <EvidenceNavigationQuoteCard
      command={command}
      quote={evidenceRecord.verified_quote}
      ariaLabel={`Highlight evidence on PDF: ${evidenceRecord.verified_quote}`}
      appearance="chat"
      accentColor={borderColor}
      debugContext={{
        source: 'chat-evidence-quote',
        chunkId: evidenceRecord.chunk_id,
        entity: evidenceRecord.entity,
        page: evidenceRecord.page,
        section: evidenceRecord.section,
        subsection: evidenceRecord.subsection ?? null,
        quote: evidenceRecord.verified_quote,
      }}
      copyButtonAriaLabel={`Copy evidence quote: ${evidenceRecord.verified_quote}`}
      onCopy={(event) => {
        event.preventDefault()
        event.stopPropagation()
        copyText(buildEvidenceQuoteCopyText(evidenceRecord)).catch((error) => {
          console.error('Failed to copy evidence quote:', error)
        })
      }}
    />
  )
}
