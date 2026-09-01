import { useEffect, useId, useMemo, useState } from 'react'

import CheckRoundedIcon from '@mui/icons-material/CheckRounded'
import CloseRoundedIcon from '@mui/icons-material/CloseRounded'
import ErrorOutlineRoundedIcon from '@mui/icons-material/ErrorOutlineRounded'
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined'
import PriorityHighRoundedIcon from '@mui/icons-material/PriorityHighRounded'
import {
  Box,
  ClickAwayListener,
  IconButton,
  Paper,
  Popper,
  Stack,
  Typography,
} from '@mui/material'
import { alpha } from '@mui/material/styles'

import type { FieldStateKind } from '@/features/curation/editor/fieldState'
import type { DomainEnvelopeEvidenceAnchorProjection } from '@/features/curation/types'

export interface HorizontalGridEvidencePopoverTarget {
  anchorEl: HTMLElement
  fieldLabel: string
  fieldValue: string
  projection: DomainEnvelopeEvidenceAnchorProjection
  state: FieldStateKind | null
  validationMessages: readonly string[]
}

export interface HorizontalGridEvidencePopoverProps {
  onClose: () => void
  target: HorizontalGridEvidencePopoverTarget | null
}

const POPPER_MODIFIERS = [
  { name: 'offset', options: { offset: [0, 9] } },
  { name: 'flip', options: { fallbackPlacements: ['top'] } },
  { name: 'preventOverflow', options: { padding: 12 } },
]

function evidenceQuote(projection: DomainEnvelopeEvidenceAnchorProjection): string {
  return projection.quote
    ?? projection.anchor.snippet_text
    ?? projection.anchor.sentence_text
    ?? projection.anchor.normalized_text
    ?? 'No quoted passage is available for this evidence anchor.'
}

function evidenceLocation(projection: DomainEnvelopeEvidenceAnchorProjection): string {
  const location = [
    projection.page_label
      ? `Page ${projection.page_label}`
      : projection.page_number
        ? `Page ${projection.page_number}`
        : null,
    projection.section_title,
    projection.subsection_title,
    projection.figure_reference,
    projection.table_reference,
  ].filter((item): item is string => Boolean(item))

  return location.length > 0 ? location.join(' · ') : 'Document location available through evidence focus'
}

function statePresentation(state: FieldStateKind | null) {
  if (state === 'resolved') {
    return { color: 'success.main', icon: <CheckRoundedIcon />, label: 'Curator validated' }
  }
  if (state === 'needs-review') {
    return { color: 'warning.main', icon: <PriorityHighRoundedIcon />, label: 'Needs review' }
  }
  if (state === 'ai-unconfirmed') {
    return { color: 'error.main', icon: <ErrorOutlineRoundedIcon />, label: 'Not validated' }
  }
  return { color: 'text.secondary', icon: <InfoOutlinedIcon />, label: 'Context evidence' }
}

export default function HorizontalGridEvidencePopover({
  onClose,
  target,
}: HorizontalGridEvidencePopoverProps) {
  const titleId = useId()
  const [arrowElement, setArrowElement] = useState<HTMLSpanElement | null>(null)
  const popperModifiers = useMemo(() => [
    ...POPPER_MODIFIERS,
    { name: 'arrow', options: { element: arrowElement, padding: 18 } },
  ], [arrowElement])

  useEffect(() => {
    if (!target) {
      return undefined
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose()
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [onClose, target])

  const presentation = statePresentation(target?.state ?? null)

  return (
    <Popper
      anchorEl={target?.anchorEl ?? null}
      modifiers={popperModifiers}
      open={target !== null}
      placement="bottom"
      sx={(theme) => ({ zIndex: theme.zIndex.modal })}
    >
      {({ placement }) => target ? (
        <ClickAwayListener onClickAway={onClose}>
          <Paper
            aria-labelledby={titleId}
            aria-modal="false"
            data-testid="horizontal-grid-evidence-popover"
            role="dialog"
            sx={(theme) => {
              const opensBelow = placement.startsWith('bottom')
              const borderColor = theme.palette.mode === 'light'
                ? theme.palette.grey[400]
                : alpha(theme.palette.common.white, 0.28)

              return {
                position: 'relative',
                width: 'min(390px, calc(100vw - 24px))',
                p: '17px',
                overflow: 'visible',
                border: `1px solid ${borderColor}`,
                borderRadius: '7px',
                backgroundColor: 'background.paper',
                boxShadow: theme.palette.mode === 'light'
                  ? '0 12px 34px rgba(24, 42, 50, 0.22)'
                  : '0 12px 34px rgba(0, 0, 0, 0.48)',
                '& .evidence-popover-arrow': {
                  position: 'absolute',
                  width: 12,
                  height: 12,
                  ...(opensBelow
                    ? { top: -6 }
                    : { bottom: -6 }),
                  '&::before': {
                    position: 'absolute',
                    inset: 0,
                    borderColor,
                    backgroundColor: 'background.paper',
                    content: '""',
                    transform: 'rotate(45deg)',
                    ...(opensBelow
                      ? { borderLeft: '1px solid', borderTop: '1px solid' }
                      : { borderBottom: '1px solid', borderRight: '1px solid' }),
                  },
                },
              }
            }}
          >
            <Box aria-hidden="true" className="evidence-popover-arrow" ref={setArrowElement} />
            <IconButton
              aria-label="Close evidence details"
              onClick={onClose}
              size="small"
              sx={{ position: 'absolute', right: 8, top: 8, width: 28, height: 28 }}
            >
              <CloseRoundedIcon sx={{ fontSize: 19 }} />
            </IconButton>

            <Stack alignItems="flex-start" direction="row" spacing="10px" sx={{ pr: '20px' }}>
              <Box
                aria-hidden="true"
                sx={{
                  alignItems: 'center',
                  color: presentation.color,
                  display: 'flex',
                  height: 18,
                  justifyContent: 'center',
                  mt: '2px',
                  width: 18,
                  '& svg': { fontSize: 18 },
                }}
              >
                {presentation.icon}
              </Box>
              <Box minWidth={0}>
                <Typography
                  color="text.secondary"
                  display="block"
                  sx={{ fontSize: 9, fontWeight: 770, letterSpacing: '0.08em', mb: '3px', textTransform: 'uppercase' }}
                >
                  Evidence &amp; validation details
                </Typography>
                <Typography id={titleId} sx={{ fontSize: 15, fontWeight: 700, lineHeight: 1.25 }}>
                  {target.fieldLabel}: {target.fieldValue}
                </Typography>
              </Box>
            </Stack>

            <Typography
              color="text.secondary"
              display="block"
              sx={{ fontSize: 9, fontWeight: 760, letterSpacing: '0.06em', mt: '14px', textTransform: 'uppercase' }}
            >
              Highlighted passage from the paper
            </Typography>
            <Box
              component="blockquote"
              sx={(theme) => ({
                m: '6px 0 12px',
                p: '11px 12px',
                borderLeft: `3px solid ${theme.palette.warning.main}`,
                backgroundColor: theme.palette.mode === 'light'
                  ? '#fff7d9'
                  : alpha(theme.palette.warning.main, 0.13),
                color: 'text.primary',
                fontFamily: 'Georgia, "Times New Roman", serif',
                fontSize: 12,
                lineHeight: 1.52,
              })}
            >
              {evidenceQuote(target.projection)}
            </Box>

            <Box sx={{ borderTop: 1, borderColor: 'divider', pt: '8px' }}>
              <Typography color="text.secondary" sx={{ fontSize: 9 }}>
                {evidenceLocation(target.projection)} · Current status: {presentation.label}
              </Typography>
              {target.validationMessages.map((message, index) => (
                <Typography color="text.secondary" key={`${index}-${message}`} sx={{ fontSize: 9, mt: 0.5 }}>
                  {message}
                </Typography>
              ))}
            </Box>
          </Paper>
        </ClickAwayListener>
      ) : null}
    </Popper>
  )
}
