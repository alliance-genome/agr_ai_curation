import { Box, Link, Typography } from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'
import type { EntityTag } from './types'

interface EvidencePreviewPaneProps {
  tag: EntityTag | null
  onShowInPdf: (tag: EntityTag) => void
}

function renderQuotedSentence(tag: EntityTag) {
  if (!tag.evidence) {
    throw new Error(`Tag ${tag.tag_id} is missing evidence for quote rendering.`)
  }

  const sentence = tag.evidence.sentence_text
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

export default function EvidencePreviewPane({ tag, onShowInPdf }: EvidencePreviewPaneProps) {
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

  if (!tag.evidence) {
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
        <Link
          component="button"
          variant="caption"
          onClick={() => onShowInPdf(tag)}
          sx={{ fontSize: '0.7rem' }}
        >
          Show in PDF
        </Link>
      </Box>

      <Box
        sx={{
          backgroundColor: alpha(theme.palette.background.default, 0.5),
          borderLeft: `3px solid ${theme.palette.primary.main}`,
          borderRadius: '0 4px 4px 0',
          p: 1.5,
          mb: 1,
        }}
      >
        <Typography variant="body2" sx={{ lineHeight: 1.6, fontSize: '0.8rem' }}>
          &ldquo;{renderQuotedSentence(tag)}&rdquo;
        </Typography>
      </Box>

      <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
        {tag.evidence.page_number != null && (
          <Typography variant="caption" color="text.secondary">Page {tag.evidence.page_number}</Typography>
        )}
        {tag.evidence.section_title && (
          <Typography variant="caption" color="text.secondary">Section: {tag.evidence.section_title}</Typography>
        )}
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
