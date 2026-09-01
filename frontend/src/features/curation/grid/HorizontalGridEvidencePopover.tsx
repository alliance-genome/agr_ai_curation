import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'

import CheckRoundedIcon from '@mui/icons-material/CheckRounded'
import CloseRoundedIcon from '@mui/icons-material/CloseRounded'
import ErrorOutlineRoundedIcon from '@mui/icons-material/ErrorOutlineRounded'
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined'
import PriorityHighRoundedIcon from '@mui/icons-material/PriorityHighRounded'
import {
  Box,
  Button,
  ClickAwayListener,
  IconButton,
  Paper,
  Popper,
  Stack,
  Typography,
} from '@mui/material'
import { alpha } from '@mui/material/styles'

import { buildNavigationCommandFromEnvelopeEvidenceProjection } from '@/features/curation/evidence'
import type { FieldStateKind } from '@/features/curation/editor/fieldState'
import type { DomainEnvelopeEvidenceAnchorProjection } from '@/features/curation/types'

export interface HorizontalGridEvidencePopoverTarget {
  anchorEl: HTMLElement
  fieldLabel: string
  fieldValue: string
  onEvidence: (projection: DomainEnvelopeEvidenceAnchorProjection) => void
  projections: readonly DomainEnvelopeEvidenceAnchorProjection[]
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
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const popperModifiers = useMemo(() => [
    ...POPPER_MODIFIERS,
    { name: 'arrow', options: { element: arrowElement, padding: 18 } },
  ], [arrowElement])
  const handleClose = useCallback(() => {
    const trigger = target?.anchorEl ?? null
    onClose()
    trigger?.focus()
  }, [onClose, target])

  useEffect(() => {
    if (!target) {
      return undefined
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        handleClose()
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [handleClose, target])

  useEffect(() => {
    if (!target) {
      return undefined
    }

    const frame = window.requestAnimationFrame(() => closeButtonRef.current?.focus())
    return () => window.cancelAnimationFrame(frame)
  }, [target])

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
        <ClickAwayListener mouseEvent="onMouseDown" onClickAway={handleClose}>
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
              onClick={handleClose}
              ref={closeButtonRef}
              size="small"
              sx={{
                backgroundColor: 'background.paper',
                position: 'absolute',
                right: 8,
                top: 8,
                width: 28,
                height: 28,
                zIndex: 1,
              }}
            >
              <CloseRoundedIcon sx={{ fontSize: 19 }} />
            </IconButton>

            <Box
              data-testid="horizontal-grid-evidence-scroll-region"
              sx={{
                maxHeight: 'min(620px, calc(100dvh - 32px))',
                overflowY: 'auto',
                overscrollBehavior: 'contain',
                p: '17px',
              }}
            >
              <Stack alignItems="flex-start" direction="row" spacing="10px" sx={{ pr: '20px' }}>
              <Box
                aria-hidden="true"
                sx={{
                  alignItems: 'center',
                  color: (theme) => target.state === 'needs-review'
                    ? (theme.palette.mode === 'dark' ? theme.palette.warning.light : '#8a5b0d')
                    : presentation.color,
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
              {target.projections.length > 0
                ? 'Highlighted passage from the paper'
                : 'Field-specific evidence'}
              </Typography>
              {target.projections.length > 0 ? (
              <Stack spacing="7px" sx={{ m: '6px 0 12px' }}>
                {target.projections.map((projection, index) => (
                  <Box
                    component="blockquote"
                    key={projection.anchor_id}
                    sx={(theme) => ({
                      m: 0,
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
                    {evidenceQuote(projection)}
                    <Typography color="text.secondary" component="footer" sx={{ fontFamily: 'inherit', fontSize: 9, mt: '5px' }}>
                      {evidenceLocation(projection)}
                    </Typography>
                    {buildNavigationCommandFromEnvelopeEvidenceProjection(projection) ? (
                      <Button
                        aria-label={`Focus evidence ${index + 1} for ${target.fieldLabel} in paper`}
                        onClick={() => target.onEvidence(projection)}
                        size="small"
                        sx={{ fontSize: 9, minHeight: 22, mt: '5px', px: '7px' }}
                        variant="text"
                      >
                        Focus in paper
                      </Button>
                    ) : null}
                  </Box>
                ))}
              </Stack>
              ) : (
              <Box
                sx={(theme) => ({
                  m: '6px 0 12px',
                  p: '11px 12px',
                  border: `1px solid ${theme.palette.divider}`,
                  borderRadius: '4px',
                  backgroundColor: theme.palette.mode === 'light'
                    ? '#f7f9f8'
                    : alpha(theme.palette.common.white, 0.04),
                })}
              >
                <Typography color="text.secondary" sx={{ fontSize: 12 }}>
                  No field-specific evidence was recorded for this field.
                </Typography>
              </Box>
              )}

              <Box
              sx={(theme) => ({
                border: `1px solid ${theme.palette.divider}`,
                borderRadius: '5px',
                backgroundColor: theme.palette.mode === 'light'
                  ? '#f7f9f8'
                  : alpha(theme.palette.common.white, 0.04),
                p: '11px 12px',
              })}
            >
              <Typography
                color="text.secondary"
                sx={{
                  fontSize: 9,
                  fontWeight: 760,
                  letterSpacing: '0.06em',
                  textTransform: 'uppercase',
                }}
              >
                Resolution
              </Typography>
              {target.state === 'resolved' ? (
                <Typography sx={{ fontSize: 12.5, fontWeight: 700, lineHeight: 1.45, mt: '4px' }}>
                  {target.fieldLabel} resolved to {target.fieldValue}.
                </Typography>
              ) : null}

              {target.validationMessages.length > 0 ? (
                <Box sx={{ mt: target.state === 'resolved' ? '9px' : '4px' }}>
                  <Typography
                    color="text.secondary"
                    sx={{ fontSize: 10, fontWeight: 700, mb: '3px' }}
                  >
                    Validator context
                  </Typography>
                  <Stack component="ul" spacing="4px" sx={{ m: 0, pl: '17px' }}>
                    {target.validationMessages.map((message, index) => (
                      <Typography
                        component="li"
                        key={`${index}:${message}`}
                        sx={{ fontSize: 12, lineHeight: 1.48, pl: '1px' }}
                      >
                        {message}
                      </Typography>
                    ))}
                  </Stack>
                </Box>
              ) : null}

              <Box
                aria-label={`Current status: ${presentation.label}`}
                sx={{
                  alignItems: 'baseline',
                  borderTop: 1,
                  borderColor: 'divider',
                  display: 'flex',
                  flexWrap: 'wrap',
                  gap: '3px 6px',
                  mt: target.validationMessages.length > 0 || target.state === 'resolved' ? '10px' : 0,
                  pt: target.validationMessages.length > 0 || target.state === 'resolved' ? '8px' : 0,
                }}
              >
                <Typography color="text.secondary" sx={{ fontSize: 10, fontWeight: 650 }}>
                  Current status
                </Typography>
                <Typography
                  data-testid="horizontal-grid-current-status"
                  sx={(theme) => ({
                    color: target.state === 'needs-review'
                      ? (theme.palette.mode === 'dark' ? theme.palette.warning.light : '#8a5b0d')
                      : presentation.color,
                    fontSize: 12,
                    fontWeight: 750,
                  })}
                >
                  {presentation.label}
                </Typography>
              </Box>
              </Box>
            </Box>
          </Paper>
        </ClickAwayListener>
      ) : null}
    </Popper>
  )
}
