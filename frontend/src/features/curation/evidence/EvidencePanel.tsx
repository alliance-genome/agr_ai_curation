import { useEffect, useMemo, useState } from 'react'

import {
  Box,
  Chip,
  Stack,
  Typography,
} from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'

import type { EvidenceLocatorQuality } from '../contracts'
import type { UseEvidenceNavigationReturn } from './useEvidenceNavigation'

type EvidencePanelNavigationProps = Pick<
  UseEvidenceNavigationReturn,
  | 'candidateEvidence'
  | 'evidenceByGroup'
  | 'hoveredEvidence'
  | 'selectEvidence'
  | 'selectedEvidence'
>

export interface EvidencePanelProps extends EvidencePanelNavigationProps {}

const ALL_FILTER_LABEL = 'All'
const DEGRADED_LOCATOR_QUALITIES = new Set<EvidenceLocatorQuality>([
  'page_only',
  'document_only',
  'unresolved',
])

function getQualityTone(
  quality: EvidenceLocatorQuality,
): 'error' | 'success' | 'warning' {
  switch (quality) {
    case 'exact_quote':
      return 'success'
    case 'normalized_quote':
    case 'section_only':
      return 'warning'
    case 'page_only':
    case 'document_only':
    case 'unresolved':
    default:
      return 'error'
  }
}

function getLocationLabel({
  pageNumber,
  sectionTitle,
}: {
  pageNumber?: number | null
  sectionTitle?: string | null
}): string {
  const parts: string[] = []

  if (pageNumber !== null && pageNumber !== undefined) {
    parts.push(`p.${pageNumber}`)
  }

  if (sectionTitle) {
    parts.push(`§${sectionTitle}`)
  }

  if (parts.length === 0) {
    return 'Document context'
  }

  return parts.join(' ')
}

function getSnippetText(
  snippetText?: string | null,
  sentenceText?: string | null,
): string {
  const resolvedText = snippetText?.trim() || sentenceText?.trim()

  return resolvedText && resolvedText.length > 0
    ? resolvedText
    : 'No evidence snippet available.'
}

export default function EvidencePanel({
  candidateEvidence,
  evidenceByGroup,
  selectedEvidence,
  hoveredEvidence,
  selectEvidence,
}: EvidencePanelProps) {
  const theme = useTheme()
  const groupFilters = useMemo(
    () => [ALL_FILTER_LABEL, ...Object.keys(evidenceByGroup)],
    [evidenceByGroup],
  )
  const [activeFilter, setActiveFilter] = useState(ALL_FILTER_LABEL)

  useEffect(() => {
    if (
      activeFilter !== ALL_FILTER_LABEL
      && evidenceByGroup[activeFilter] === undefined
    ) {
      setActiveFilter(ALL_FILTER_LABEL)
    }
  }, [activeFilter, evidenceByGroup])

  const visibleEvidence = useMemo(
    () => (
      activeFilter === ALL_FILTER_LABEL
        ? candidateEvidence
        : evidenceByGroup[activeFilter] ?? []
    ),
    [activeFilter, candidateEvidence, evidenceByGroup],
  )

  return (
    <Box
      sx={{
        flex: 1,
        minHeight: 0,
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <Stack
        spacing={1.25}
        sx={{
          px: 2,
          py: 1.5,
          borderBottom: `1px solid ${alpha(theme.palette.divider, 0.72)}`,
        }}
      >
        <Stack
          alignItems={{ xs: 'flex-start', sm: 'center' }}
          direction={{ xs: 'column', sm: 'row' }}
          justifyContent="space-between"
          spacing={1}
        >
          <Typography variant="subtitle1">
            Evidence Anchors ({candidateEvidence.length})
          </Typography>
          {activeFilter !== ALL_FILTER_LABEL ? (
            <Typography color="text.secondary" variant="caption">
              Showing {visibleEvidence.length} in {activeFilter}
            </Typography>
          ) : null}
        </Stack>

        <Stack direction="row" flexWrap="wrap" spacing={0.75} useFlexGap>
          {groupFilters.map((filter) => {
            const isActive = filter === activeFilter

            return (
              <Chip
                clickable
                color={isActive ? 'primary' : 'default'}
                key={filter}
                label={filter}
                onClick={() => setActiveFilter(filter)}
                size="small"
                variant={isActive ? 'filled' : 'outlined'}
              />
            )
          })}
        </Stack>
      </Stack>

      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          overflowY: 'auto',
          px: 2,
          py: 1.5,
        }}
      >
        {visibleEvidence.length === 0 ? (
          <Box
            sx={{
              minHeight: '100%',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              borderRadius: 1.5,
              border: `1px dashed ${alpha(theme.palette.divider, 0.72)}`,
              px: 2,
              py: 3,
              textAlign: 'center',
            }}
          >
            <Typography color="text.secondary" variant="body2">
              {candidateEvidence.length === 0
                ? 'No evidence anchors are available for this candidate.'
                : 'No evidence anchors match the current filter.'}
            </Typography>
          </Box>
        ) : (
          <Stack spacing={1.25}>
            {visibleEvidence.map((record) => {
              const qualityTone = getQualityTone(record.anchor.locator_quality)
              const isSelected = selectedEvidence?.anchor_id === record.anchor_id
              const isHovered = hoveredEvidence?.anchor_id === record.anchor_id
              const isActive = isSelected || isHovered

              return (
                <Box
                  aria-pressed={isSelected}
                  component="button"
                  data-active={isActive ? 'true' : 'false'}
                  data-hovered={isHovered ? 'true' : 'false'}
                  data-testid={`evidence-card-${record.anchor_id}`}
                  key={record.anchor_id}
                  onClick={() => selectEvidence(record)}
                  type="button"
                  sx={{
                    width: '100%',
                    borderRadius: 1.5,
                    border: `1px solid ${
                      isSelected
                        ? alpha(theme.palette.primary.main, 0.9)
                        : isHovered
                          ? alpha(theme.palette.info.main, 0.8)
                          : alpha(theme.palette.divider, 0.72)
                    }`,
                    backgroundColor: isSelected
                      ? alpha(theme.palette.primary.main, 0.16)
                      : isHovered
                        ? alpha(theme.palette.info.main, 0.12)
                        : alpha(theme.palette.common.white, 0.02),
                    px: 1.5,
                    py: 1.25,
                    textAlign: 'left',
                    color: 'inherit',
                    cursor: 'pointer',
                    transition: 'border-color 0.2s ease, background-color 0.2s ease',
                    '&:hover': {
                      borderColor: alpha(theme.palette.primary.main, 0.72),
                      backgroundColor: isSelected
                        ? alpha(theme.palette.primary.main, 0.18)
                        : alpha(theme.palette.primary.main, 0.1),
                    },
                    '&:focus-visible': {
                      outline: `2px solid ${theme.palette.primary.main}`,
                      outlineOffset: 2,
                    },
                  }}
                >
                  <Stack spacing={1}>
                    <Stack
                      alignItems={{ xs: 'flex-start', sm: 'center' }}
                      direction={{ xs: 'column', sm: 'row' }}
                      justifyContent="space-between"
                      spacing={1}
                    >
                      <Stack
                        alignItems="center"
                        direction="row"
                        flexWrap="wrap"
                        spacing={0.75}
                        useFlexGap
                      >
                        <Chip
                          data-quality-tone={qualityTone}
                          label={record.anchor.locator_quality}
                          size="small"
                          sx={{
                            height: 22,
                            border: `1px solid ${alpha(
                              theme.palette[qualityTone].main,
                              0.28,
                            )}`,
                            backgroundColor: alpha(
                              theme.palette[qualityTone].main,
                              0.18,
                            ),
                            color: theme.palette[qualityTone].light,
                            '& .MuiChip-label': {
                              px: 1,
                              fontWeight: 600,
                            },
                          }}
                        />
                        <Typography color="text.secondary" variant="caption">
                          {getLocationLabel({
                            pageNumber: record.anchor.page_number,
                            sectionTitle: record.anchor.section_title,
                          })}
                        </Typography>
                      </Stack>

                      {isSelected ? (
                        <Typography color="primary.main" variant="caption">
                          Focused in viewer
                        </Typography>
                      ) : null}

                      {!isSelected && isHovered ? (
                        <Typography color="info.main" variant="caption">
                          Highlighted in PDF
                        </Typography>
                      ) : null}
                    </Stack>

                    {record.field_group_keys.length > 0 ? (
                      <Stack direction="row" flexWrap="wrap" spacing={0.75} useFlexGap>
                        {[...new Set(record.field_group_keys)].map((fieldGroupKey) => (
                          <Chip
                            key={`${record.anchor_id}-${fieldGroupKey}`}
                            label={fieldGroupKey}
                            size="small"
                            variant="outlined"
                          />
                        ))}
                      </Stack>
                    ) : null}

                    <Typography
                      sx={{ lineHeight: 1.55 }}
                      variant="body2"
                    >
                      {getSnippetText(
                        record.anchor.snippet_text,
                        record.anchor.sentence_text,
                      )}
                    </Typography>

                    {DEGRADED_LOCATOR_QUALITIES.has(record.anchor.locator_quality) ? (
                      <Typography color="warning.main" variant="caption">
                        Could not resolve exact quote - will jump to best available location
                      </Typography>
                    ) : null}
                  </Stack>
                </Box>
              )
            })}
          </Stack>
        )}
      </Box>
    </Box>
  )
}
