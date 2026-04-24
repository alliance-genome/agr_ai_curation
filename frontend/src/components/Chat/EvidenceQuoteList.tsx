import { useCallback, useEffect, useRef } from 'react'
import { Box, Collapse } from '@mui/material'
import { alpha } from '@mui/material/styles'

import type { EvidenceRecord } from '@/features/curation/types'

import EvidenceQuote from './EvidenceQuote'

export interface EvidenceQuoteGroup {
  entity: string
  evidenceRecords: EvidenceRecord[]
  colorHex: string
}

interface EvidenceQuoteListProps {
  groups: EvidenceQuoteGroup[]
  activeEntity: string | null
  onReviewAndCurateClick?: (() => void) | null
  interactive?: boolean
  quoteTestId?: string
}

export default function EvidenceQuoteList({
  groups,
  activeEntity,
  onReviewAndCurateClick,
  interactive = true,
  quoteTestId,
}: EvidenceQuoteListProps) {
  const scrollAnchorRefs = useRef(new Map<string, HTMLDivElement>())
  const pendingScrollTimeoutRef = useRef<number | null>(null)

  const scrollExpandedEvidenceIntoView = useCallback((entity: string) => {
    const target = scrollAnchorRefs.current.get(entity)
    if (typeof target?.scrollIntoView === 'function') {
      target.scrollIntoView({
        behavior: 'smooth',
        block: 'end',
        inline: 'nearest',
      })
    }
  }, [])

  const setScrollAnchorRef = useCallback(
    (entity: string) => (node: HTMLDivElement | null) => {
      if (node) {
        scrollAnchorRefs.current.set(entity, node)
        return
      }

      scrollAnchorRefs.current.delete(entity)
    },
    [],
  )

  useEffect(() => {
    if (!activeEntity) {
      return undefined
    }

    pendingScrollTimeoutRef.current = window.setTimeout(() => {
      scrollExpandedEvidenceIntoView(activeEntity)
      pendingScrollTimeoutRef.current = null
    }, 0)

    return () => {
      if (pendingScrollTimeoutRef.current !== null) {
        window.clearTimeout(pendingScrollTimeoutRef.current)
        pendingScrollTimeoutRef.current = null
      }
    }
  }, [activeEntity, scrollExpandedEvidenceIntoView])

  const activeGroup = activeEntity
    ? groups.find((group) => group.entity === activeEntity) ?? null
    : null

  return (
    <Collapse
      in={Boolean(activeGroup)}
      onEntered={() => {
        if (activeGroup) {
          scrollExpandedEvidenceIntoView(activeGroup.entity)
        }
      }}
      timeout="auto"
      unmountOnExit
    >
      {activeGroup ? (
        <Box
          sx={{
            display: 'flex',
            flexDirection: 'column',
            gap: '6px',
            mt: '10px',
            pb: '4px',
          }}
        >
          {activeGroup.evidenceRecords.map((record, index) => (
            <EvidenceQuote
              borderColor={activeGroup.colorHex}
              evidenceRecord={record}
              interactive={interactive}
              key={`${activeGroup.entity}-${record.chunk_id}-${index}`}
              testId={quoteTestId}
            />
          ))}

          {interactive ? (
            <Box
              sx={{
                mt: '8px',
                pt: '8px',
                borderTop: (theme) => `1px solid ${alpha(theme.palette.divider, 0.85)}`,
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
              }}
            >
              <Box
                sx={{
                  fontSize: '11px',
                  color: 'text.secondary',
                }}
              >
                Full evidence review with PDF highlighting →
              </Box>

              {onReviewAndCurateClick ? (
                <Box
                  component="button"
                  onClick={onReviewAndCurateClick}
                  sx={{
                    p: 0,
                    border: 0,
                    background: 'transparent',
                    color: 'primary.main',
                    cursor: 'pointer',
                    fontSize: '11px',
                  }}
                  type="button"
                >
                  Review & Curate
                </Box>
              ) : null}
            </Box>
          ) : null}

          <Box
            aria-hidden="true"
            ref={setScrollAnchorRef(activeGroup.entity)}
            sx={{ height: 1, scrollMarginBottom: '20px' }}
          />
        </Box>
      ) : null}
    </Collapse>
  )
}
