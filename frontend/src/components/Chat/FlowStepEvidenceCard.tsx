import { Box } from '@mui/material'
import { alpha } from '@mui/material/styles'

import type { FlowStepEvidenceDetails } from '@/types/AuditEvent'

import EvidenceCard from './EvidenceCard'

interface FlowStepEvidenceCardProps {
  details: FlowStepEvidenceDetails
  interactionMode?: 'interactive' | 'readOnly'
  containerTestId?: string
  emptyStateTestId?: string
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

export default function FlowStepEvidenceCard({
  details,
  interactionMode = 'interactive',
  containerTestId = 'flow-step-evidence-card',
  emptyStateTestId = 'flow-step-evidence-empty',
}: FlowStepEvidenceCardProps) {
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
      data-testid={containerTestId}
      sx={{
        alignSelf: 'flex-start',
        display: 'flex',
        flexDirection: 'column',
        maxWidth: '85%',
        minWidth: 0,
      }}
    >
      <Box
        sx={(theme) => ({
          backgroundColor: theme.palette.mode === 'dark'
            ? alpha(theme.palette.common.white, 0.08)
            : alpha(theme.palette.text.primary, 0.06),
          borderRadius: '18px 18px 4px 4px',
          color: theme.palette.text.primary,
          px: '1rem',
          py: '0.85rem',
        })}
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
        <EvidenceCard
          evidenceRecords={evidenceRecords}
          headerLabel={
            isPreviewSubset
              ? formatEvidencePreviewCount(previewCount)
              : formatEvidenceQuoteCount(previewCount)
          }
          interactionMode={interactionMode}
        />
      ) : (
        <Box
          data-testid={emptyStateTestId}
          sx={(theme) => ({
            backgroundColor: theme.palette.mode === 'dark'
              ? theme.palette.secondary.dark
              : alpha(theme.palette.secondary.main, 0.09),
            borderRadius: '0 0 18px 4px',
            borderTop: `1px solid ${alpha(theme.palette.divider, 0.85)}`,
            color: theme.palette.text.secondary,
            fontSize: '12px',
            lineHeight: 1.45,
            px: '1rem',
            py: '10px',
          })}
        >
          No quote previews were attached to this step.
        </Box>
      )}
    </Box>
  )
}
