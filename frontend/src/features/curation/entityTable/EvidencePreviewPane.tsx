import { Box, Link, Typography } from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'
import type { CurationEvidenceRecord } from '@/features/curation/types'
import type { EntityTag } from './types'

interface EvidencePreviewPaneProps {
  tag: EntityTag | null
  evidenceRecords?: CurationEvidenceRecord[]
  onShowInPdf: (tag: EntityTag, evidence?: CurationEvidenceRecord | null) => void
}

interface EvidencePreviewRecord {
  id: string
  sentenceText: string
  pageNumber: number | null
  sectionTitle: string | null
  evidenceRecord: CurationEvidenceRecord | null
}

function renderQuotedSentence(tag: EntityTag, sentence: string) {
  if (tag.entity_name.trim().length === 0) {
    return sentence
  }

  const matchIndex = sentence.toLowerCase().indexOf(tag.entity_name.toLowerCase())
  if (matchIndex < 0) {
    return sentence
  }

  const matchEnd = matchIndex + tag.entity_name.length

  return (
    <>
      {sentence.slice(0, matchIndex)}
      <strong>{sentence.slice(matchIndex, matchEnd)}</strong>
      {sentence.slice(matchEnd)}
    </>
  )
}

function buildEvidencePreviewRecords(
  tag: EntityTag,
  evidenceRecords: CurationEvidenceRecord[],
): EvidencePreviewRecord[] {
  const records = [...evidenceRecords]
    .sort((left, right) => Number(right.is_primary) - Number(left.is_primary))
    .map((record) => {
      const sentenceText = record.anchor.sentence_text?.trim() || record.anchor.snippet_text?.trim() || ''
      if (!sentenceText) {
        return null
      }

      return {
        id: record.anchor_id,
        sentenceText,
        pageNumber: record.anchor.page_number ?? null,
        sectionTitle: record.anchor.section_title ?? null,
        evidenceRecord: record,
      }
    })
    .filter((record): record is EvidencePreviewRecord => record !== null)

  if (records.length > 0) {
    return records
  }

  if (!tag.evidence) {
    return []
  }

  return [
    {
      id: `legacy:${tag.tag_id}`,
      sentenceText: tag.evidence.sentence_text,
      pageNumber: tag.evidence.page_number,
      sectionTitle: tag.evidence.section_title,
      evidenceRecord: null,
    },
  ]
}

export default function EvidencePreviewPane({
  tag,
  evidenceRecords = [],
  onShowInPdf,
}: EvidencePreviewPaneProps) {
  const theme = useTheme()

  if (!tag) {
    return (
      <Box sx={{ p: 2, display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
        <Typography variant="body2" color="text.secondary">
          Select a row to view evidence.
        </Typography>
      </Box>
    )
  }

  const previewRecords = buildEvidencePreviewRecords(tag, evidenceRecords)

  if (previewRecords.length === 0) {
    return (
      <Box sx={{ p: 2 }}>
        <Typography variant="overline" color="text.secondary">
          Evidence for <strong>{tag.entity_name}</strong>
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
          {tag.source === 'manual'
            ? 'No AI evidence — manually added.'
            : 'No evidence is available for this entity.'}
        </Typography>
      </Box>
    )
  }

  return (
    <Box sx={{ p: 1.5, height: '100%', display: 'flex', flexDirection: 'column', overflow: 'auto' }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
        <Typography variant="overline" color="text.secondary" sx={{ letterSpacing: 0.5, fontSize: '0.65rem' }}>
          Evidence for <strong style={{ color: theme.palette.text.primary }}>{tag.entity_name}</strong>
        </Typography>
      </Box>

      {previewRecords.map((record) => (
        <Box
          key={record.id}
          sx={{
            backgroundColor: alpha(theme.palette.background.default, 0.5),
            borderLeft: `3px solid ${theme.palette.primary.main}`,
            borderRadius: '0 4px 4px 0',
            p: 1.5,
            mb: 1,
          }}
        >
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 1, mb: 0.75 }}>
            <Typography variant="caption" color="text.secondary">
              {record.pageNumber != null ? `Page ${record.pageNumber}` : 'Page unavailable'}
              {record.sectionTitle ? ` · ${record.sectionTitle}` : ''}
            </Typography>
            <Link
              component="button"
              variant="caption"
              onClick={() => onShowInPdf(tag, record.evidenceRecord)}
              sx={{ fontSize: '0.7rem' }}
            >
              Show in PDF
            </Link>
          </Box>
          <Typography variant="body2" sx={{ lineHeight: 1.6, fontSize: '0.8rem' }}>
            &ldquo;{renderQuotedSentence(tag, record.sentenceText)}&rdquo;
          </Typography>
        </Box>
      ))}

      <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
        <Typography variant="caption" color="text.secondary">
          {previewRecords.length === 1 ? '1 evidence quote' : `${previewRecords.length} evidence quotes`}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {tag.source === 'ai' ? 'AI-extracted' : 'Manually added'}
        </Typography>
        {tag.db_entity_id && (
          <Typography variant="caption" sx={{ color: theme.palette.success.main }}>
            {tag.db_entity_id}
          </Typography>
        )}
      </Box>
    </Box>
  )
}
