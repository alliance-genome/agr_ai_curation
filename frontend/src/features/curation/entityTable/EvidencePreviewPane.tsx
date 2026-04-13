import { Box, Typography } from '@mui/material'
import { useTheme } from '@mui/material/styles'
import EvidenceNavigationQuoteCard from '@/features/curation/evidence/EvidenceNavigationQuoteCard'
import { deriveNavigationQuoteFromAnchor } from '@/features/curation/evidence/navigationSourceAdapters'
import type { CurationEvidenceRecord } from '@/features/curation/types'
import { buildEntityTagNavigationCommand } from './entityTagNavigation'
import type { EntityTag } from './types'

interface EvidencePreviewPaneProps {
  tag: EntityTag | null
  evidenceRecords?: CurationEvidenceRecord[]
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
      const sentenceText = deriveNavigationQuoteFromAnchor(record.anchor) ?? ''
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
        <Box key={record.id} sx={{ mb: 1 }}>
          {(() => {
            const command = buildEntityTagNavigationCommand(tag, record.evidenceRecord)
            if (!command) {
              return (
                <Typography variant="body2" sx={{ lineHeight: 1.6, fontSize: '0.8rem' }}>
                  &ldquo;{renderQuotedSentence(tag, record.sentenceText)}&rdquo;
                </Typography>
              )
            }

            return (
              <EvidenceNavigationQuoteCard
                command={command}
                quote={record.sentenceText}
                quoteContent={renderQuotedSentence(tag, record.sentenceText)}
                ariaLabel={`Highlight evidence on PDF: ${record.sentenceText}`}
                appearance="workspace"
                accentColor={theme.palette.primary.main}
                debugContext={{
                  source: 'curation-evidence-preview',
                  tagId: tag.tag_id,
                  anchorId: record.evidenceRecord?.anchor_id ?? null,
                  pageNumber: record.pageNumber,
                  sectionTitle: record.sectionTitle,
                  quote: record.sentenceText,
                }}
              />
            )
          })()}
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
