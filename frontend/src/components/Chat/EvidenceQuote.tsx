import { Box } from '@mui/material'
import { alpha } from '@mui/material/styles'

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

function buildMetadataLabel(evidenceRecord: EvidenceRecord): string | null {
  return buildEvidenceLocationLabel({
    pageNumber: evidenceRecord.page,
    sectionTitle: evidenceRecord.section,
    subsectionTitle: evidenceRecord.subsection ?? null,
  })
}

function buildEvidenceQuoteCopyText(evidenceRecord: EvidenceRecord): string {
  const label = buildMetadataLabel(evidenceRecord)
  const quote = `"${evidenceRecord.verified_quote.trim()}"`
  return label ? `${label}\n${quote}` : quote
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
        sx={(theme) => ({
          backgroundColor: theme.palette.mode === 'dark'
            ? alpha(theme.palette.common.white, 0.08)
            : alpha(theme.palette.background.paper, 0.72),
          borderRadius: '8px',
          borderLeft: `3px solid ${borderColor}`,
          px: '12px',
          py: '10px',
        })}
      >
        {buildMetadataLabel(evidenceRecord) ? (
          <Box
            sx={{
              fontSize: '11px',
              color: 'text.secondary',
              mb: '4px',
            }}
          >
            {buildMetadataLabel(evidenceRecord)}
          </Box>
        ) : null}

        <Box
          sx={{
            fontSize: '13px',
            fontStyle: 'italic',
            lineHeight: 1.5,
            color: 'text.primary',
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
      ariaLabel={`Show this evidence in the paper: ${evidenceRecord.verified_quote}`}
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
