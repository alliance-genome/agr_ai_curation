import { Box } from '@mui/material'

import type { FlowStepEvidenceDetails } from '@/types/AuditEvent'

import EvidenceCard from './EvidenceCard'

interface FlowStepEvidenceCardProps {
  details: FlowStepEvidenceDetails
}

function formatEvidenceQuoteCount(count: number): string {
  return count === 1 ? '1 evidence quote' : `${count} evidence quotes`
}

export default function FlowStepEvidenceCard({
  details,
}: FlowStepEvidenceCardProps) {
  const evidenceRecords = details.evidence_records ?? []
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
      data-testid="flow-step-evidence-card"
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
          {formatEvidenceQuoteCount(details.evidence_count)} captured in this step.
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
        <EvidenceCard evidenceRecords={evidenceRecords} />
      ) : (
        <Box
          data-testid="flow-step-evidence-empty"
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
