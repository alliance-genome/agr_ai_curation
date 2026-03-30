import { useCallback, useRef } from 'react'
import { Box, Collapse } from '@mui/material'

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
}

export default function EvidenceQuoteList({
  groups,
  activeEntity,
  onReviewAndCurateClick,
}: EvidenceQuoteListProps) {
  const scrollAnchorRefs = useRef(new Map<string, HTMLDivElement>())

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
    // This intentionally returns a per-entity callback ref so the map tracks
    // whichever evidence group is currently mounted after Collapse transitions.
    (entity: string) => (node: HTMLDivElement | null) => {
      if (node) {
        scrollAnchorRefs.current.set(entity, node)
        return
      }

      scrollAnchorRefs.current.delete(entity)
    },
    [],
  )

  return (
    <>
      {groups.map((group) => {
        const isActive = activeEntity === group.entity

        return (
          <Collapse
            in={isActive}
            key={group.entity}
            onEntered={() => {
              scrollExpandedEvidenceIntoView(group.entity)
            }}
            timeout="auto"
            unmountOnExit
          >
            <Box
              sx={{
                display: 'flex',
                flexDirection: 'column',
                gap: '6px',
                mt: '10px',
                pb: '4px',
              }}
            >
              {group.evidenceRecords.map((record, index) => (
                <EvidenceQuote
                  borderColor={group.colorHex}
                  evidenceRecord={record}
                  key={`${group.entity}-${record.chunk_id}-${index}`}
                />
              ))}

              <Box
                sx={{
                  mt: '8px',
                  pt: '8px',
                  borderTop: '1px solid rgba(255, 255, 255, 0.1)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                }}
              >
                <Box
                  sx={{
                    fontSize: '11px',
                    color: 'rgba(255, 255, 255, 0.5)',
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
                      color: '#90caf9',
                      cursor: 'pointer',
                      fontSize: '11px',
                    }}
                    type="button"
                  >
                    Review & Curate
                  </Box>
                ) : null}
              </Box>

              <Box
                aria-hidden="true"
                ref={setScrollAnchorRef(group.entity)}
                sx={{ height: 1, scrollMarginBottom: '20px' }}
              />
            </Box>
          </Collapse>
        )
      })}
    </>
  )
}
