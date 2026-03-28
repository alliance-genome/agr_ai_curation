import { Box } from '@mui/material'

import type { EvidenceRecord } from '@/features/curation/types'

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
      sx={{
        backgroundColor: 'rgba(255, 255, 255, 0.08)',
        borderRadius: '8px',
        px: '12px',
        py: '10px',
        borderLeft: `3px solid ${borderColor}`,
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
          lineHeight: 1.4,
          color: 'rgba(255, 255, 255, 0.9)',
        }}
      >
        "{evidenceRecord.verified_quote}"
      </Box>
    </Box>
  )
}
