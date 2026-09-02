/**
 * EnvelopeFieldTable
 *
 * One real table for the fields of a selected envelope object. Four columns:
 * Req, Field, Type and source, Automatic check. Rows are grouped under the
 * domain pack's field-group labels when present.
 */

import { useEffect, useRef } from 'react'
import {
  Box,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material'
import { alpha } from '@mui/material/styles'

import type { DomainEnvelopeFieldMetadata, ValidationAttachmentOption } from '@/services/agentStudioService'
import type { EnvelopeFieldGroupView, ProviderWordResolver } from './envelopePresentation'
import { fieldTypeLabel, validatorPolicyBadge } from './envelopePresentation'
import { MONO_FONT_FAMILY, StateDot, tableHeadCellSx as headCellSx } from './agentGuidePrimitives'

interface EnvelopeFieldTableProps {
  groups: EnvelopeFieldGroupView[]
  /** Accessible name for the table. */
  ariaLabel: string
  /** Names a field's source-of-truth provider from the pack's schema refs. */
  providerWord: ProviderWordResolver
  /** Hide the type column when the panel is narrow. */
  narrow?: boolean
  maxHeight?: number | string
  /** Marks and scrolls to one field row, from a deep link. */
  highlightFieldPath?: string
}


const bodyCellSx = {
  fontSize: 13,
  py: 0.75,
  verticalAlign: 'middle',
  borderBottom: 1,
  borderColor: 'divider',
} as const

function PolicyBadge({ attachment }: { attachment: ValidationAttachmentOption }) {
  const badge = validatorPolicyBadge(attachment)
  if (!badge) return null
  const tone = badge === 'Blocking' ? 'error' : 'warning'
  return (
    <Box
      component="span"
      sx={{
        fontSize: 10.5,
        px: 0.625,
        borderRadius: 0.5,
        fontWeight: 600,
        letterSpacing: '0.04em',
        textTransform: 'uppercase',
        flex: 'none',
        color: `${tone}.main`,
        backgroundColor: (theme) => alpha(theme.palette[tone].main, theme.palette.mode === 'dark' ? 0.14 : 0.1),
      }}
    >
      {badge}
    </Box>
  )
}

function AutomaticCheckCell({ field }: { field: DomainEnvelopeFieldMetadata }) {
  if (field.validation_attachments.length === 0) {
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, color: 'text.disabled', fontSize: 12.5 }}>
        <StateDot tone="none" />
        <span>Not checked</span>
      </Box>
    )
  }
  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.25 }}>
      {field.validation_attachments.map((attachment) => (
        <Box
          key={attachment.attachment_id}
          sx={{ display: 'flex', alignItems: 'center', gap: 0.75, fontSize: 12.5, minWidth: 0 }}
        >
          <StateDot tone={attachment.state} />
          <Box component="span" sx={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={attachment.label}>
            {attachment.label}
          </Box>
          <PolicyBadge attachment={attachment} />
        </Box>
      ))}
    </Box>
  )
}

function FieldRow({
  field,
  narrow,
  providerWord,
  highlighted,
}: {
  field: DomainEnvelopeFieldMetadata
  narrow: boolean
  providerWord: ProviderWordResolver
  highlighted: boolean
}) {
  const label = field.display_name || field.field_path
  const rowRef = useRef<HTMLTableRowElement>(null)

  useEffect(() => {
    if (highlighted) rowRef.current?.scrollIntoView({ block: 'center' })
  }, [highlighted])

  return (
    <TableRow ref={rowRef} selected={highlighted} aria-current={highlighted ? 'true' : undefined}>
      <TableCell sx={{ ...bodyCellSx, textAlign: 'center', px: 0.5, color: 'error.main', fontWeight: 600 }}>
        {field.required ? <span role="img" aria-label="Required">•</span> : null}
      </TableCell>
      <TableCell sx={{ ...bodyCellSx, minWidth: 0, maxWidth: 0, width: narrow ? '55%' : '34%' }}>
        <Box sx={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
          <Box component="span" sx={{ fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={label}>
            {label}
          </Box>
          <Box
            component="code"
            title={field.field_path}
            sx={{
              fontFamily: MONO_FONT_FAMILY,
              fontSize: 11.5,
              color: 'text.disabled',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {field.field_path}
          </Box>
        </Box>
      </TableCell>
      {!narrow && (
        <TableCell sx={{ ...bodyCellSx, color: 'text.secondary', fontSize: 12.5 }}>
          {fieldTypeLabel(field)}
          <Box component="span" sx={{ color: 'text.disabled' }}> · {providerWord(field.source_of_truth)}</Box>
        </TableCell>
      )}
      <TableCell sx={{ ...bodyCellSx, minWidth: 0, maxWidth: 0, width: narrow ? '45%' : '30%' }}>
        <AutomaticCheckCell field={field} />
      </TableCell>
    </TableRow>
  )
}

function EnvelopeFieldTable({ groups, ariaLabel, providerWord, narrow = false, maxHeight = 480, highlightFieldPath }: EnvelopeFieldTableProps) {
  const columnCount = narrow ? 3 : 4
  const hasFields = groups.some((group) => group.fields.length > 0)

  if (!hasFields) {
    return (
      <Typography variant="body2" color="text.secondary" sx={{ py: 1 }}>
        This object declares no fields.
      </Typography>
    )
  }

  return (
    <TableContainer
      sx={{
        maxHeight,
        border: 1,
        borderColor: 'divider',
        borderRadius: 2,
      }}
    >
      <Table stickyHeader size="small" aria-label={ariaLabel} sx={{ tableLayout: 'fixed' }}>
        <TableHead>
          <TableRow>
            <TableCell sx={{ ...headCellSx, width: 28, px: 0.5, textAlign: 'center' }}>
              <abbr title="Required" style={{ textDecoration: 'none' }}>Req</abbr>
            </TableCell>
            <TableCell sx={headCellSx}>Field</TableCell>
            {!narrow && <TableCell sx={headCellSx}>Type · source</TableCell>}
            <TableCell sx={headCellSx}>Automatic check</TableCell>
          </TableRow>
        </TableHead>
        {groups.map((group) => (
          <TableBody key={group.id}>
            {group.label && (
              <TableRow>
                <TableCell
                  component="th"
                  scope="rowgroup"
                  colSpan={columnCount}
                  sx={{
                    ...headCellSx,
                    backgroundColor: 'background.default',
                    py: 0.5,
                    textAlign: 'left',
                  }}
                >
                  {group.label}
                </TableCell>
              </TableRow>
            )}
            {group.fields.map((field) => (
              <FieldRow
                key={field.field_path}
                field={field}
                narrow={narrow}
                providerWord={providerWord}
                highlighted={field.field_path === highlightFieldPath}
              />
            ))}
          </TableBody>
        ))}
      </Table>
    </TableContainer>
  )
}

export default EnvelopeFieldTable
