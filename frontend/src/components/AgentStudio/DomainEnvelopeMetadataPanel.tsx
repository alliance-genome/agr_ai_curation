import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Chip,
  Divider,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material'
import { alpha } from '@mui/material/styles'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import type { ReactNode } from 'react'

import type {
  DomainEnvelopeFieldMetadata,
  DomainEnvelopeMetadata,
  DomainEnvelopeObjectMetadata,
  DomainEnvelopeSchemaRef,
  ValidationAttachmentOption,
} from '@/services/agentStudioService'
import type { ValidationAttachmentSelection } from './FlowBuilder/types'

type ValidationAttachmentView = ValidationAttachmentOption | ValidationAttachmentSelection

interface DomainEnvelopeMetadataPanelProps {
  metadata?: DomainEnvelopeMetadata | null
  validationAttachments?: ValidationAttachmentView[]
  compact?: boolean
  layout?: 'standard' | 'flow-editor'
  title?: string
  validationModeNote?: string
}

interface SectionAccordionProps {
  title: string
  summary?: string
  defaultExpanded?: boolean
  children: ReactNode
}

interface SourceTruthFieldNote {
  objectLabel: string
  fieldLabel: string
  provider: string
}

interface SourceTruthNotes {
  objectNotes: string[]
  fieldNotes: SourceTruthFieldNote[]
  otherNotes: string[]
}

const monoTextSx = {
  fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
  overflowWrap: 'anywhere',
  wordBreak: 'break-word',
} as const

const compactChipSx = {
  height: 18,
  fontSize: '0.6rem',
  maxWidth: '100%',
  '& .MuiChip-label': {
    minWidth: 0,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  },
} as const

function humanizeState(value: string): string {
  if (value === 'blocked') return 'not available'
  return value.replace(/_/g, ' ')
}

function chipColorForState(
  state?: string | null
): 'default' | 'success' | 'warning' | 'error' {
  if (state === 'active' || state === 'stable') return 'success'
  if (state === 'planned' || state === 'draft' || state === 'in_development') return 'warning'
  if (state === 'blocked' || state === 'deprecated') return 'error'
  return 'default'
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function formatSchemaRef(schemaRef?: DomainEnvelopeSchemaRef | null): string {
  if (!schemaRef) return ''
  return [
    schemaRef.provider,
    schemaRef.name || schemaRef.schema_id,
    schemaRef.version,
  ].filter(Boolean).join(' / ')
}

function formatProviderRef(providerKey: string, value: unknown): string {
  if (!isRecord(value)) return providerKey

  const detailKeys = [
    'schema_ref',
    'source_file',
    'class',
    'slot',
    'range',
    'root_schema',
    'commit',
    'repository',
  ]
  const details = detailKeys
    .map((key) => value[key])
    .filter((entry): entry is string => typeof entry === 'string' && entry.trim().length > 0)

  return details.length > 0 ? `${providerKey}: ${details.join(' / ')}` : providerKey
}

function stripTrailingPeriod(value: string): string {
  return value.trim().replace(/\.$/, '')
}

function splitSourceTruthNotes(notes: string[]): SourceTruthNotes {
  return notes.reduce<SourceTruthNotes>(
    (groups, note) => {
      const trimmed = note.trim()
      const fieldMatch = trimmed.match(/^(.+?)\s+\/\s+(.+?):\s+source of truth is\s+(.+?)\.?$/i)
      if (fieldMatch) {
        groups.fieldNotes.push({
          objectLabel: fieldMatch[1].trim(),
          fieldLabel: fieldMatch[2].trim(),
          provider: stripTrailingPeriod(fieldMatch[3]),
        })
        return groups
      }

      const objectMatch = trimmed.match(/^([^:]+):\s+(.+)$/)
      if (objectMatch) {
        groups.objectNotes.push(stripTrailingPeriod(objectMatch[2]))
        return groups
      }

      if (trimmed) {
        groups.otherNotes.push(stripTrailingPeriod(trimmed))
      }
      return groups
    },
    { objectNotes: [], fieldNotes: [], otherNotes: [] }
  )
}

function validationStateCounts(attachments: ValidationAttachmentView[]) {
  return attachments.reduce(
    (counts, attachment) => {
      counts[attachment.state] += 1
      if ('enabled' in attachment && attachment.enabled && attachment.state === 'active') {
        counts.enabled += 1
      }
      if (
        'enabled' in attachment
        && !attachment.enabled
        && attachment.state === 'active'
        && (attachment.required || attachment.export_blocking)
      ) {
        counts.optedOut += 1
      }
      return counts
    },
    { active: 0, planned: 0, blocked: 0, enabled: 0, optedOut: 0 }
  )
}

function GuidanceCard({
  label,
  children,
  tone = 'default',
}: {
  label: string
  children: ReactNode
  tone?: 'default' | 'info' | 'success'
}) {
  const toneColor = tone === 'success' ? 'success.main' : tone === 'info' ? 'info.main' : 'text.secondary'
  return (
    <Box
      sx={{
        p: 1,
        borderRadius: 1,
        border: (theme) => `1px solid ${alpha(theme.palette.divider, 0.72)}`,
        backgroundColor: (theme) => alpha(theme.palette.background.paper, 0.5),
      }}
    >
      <Typography
        variant="caption"
        sx={{
          display: 'block',
          mb: 0.35,
          color: toneColor,
          fontSize: '0.6rem',
          fontWeight: 750,
          textTransform: 'uppercase',
          letterSpacing: 0.4,
        }}
      >
        {label}
      </Typography>
      <Typography
        component="div"
        variant="body2"
        color="text.secondary"
        sx={{ fontSize: '0.75rem', lineHeight: 1.45, textWrap: 'pretty' }}
      >
        {children}
      </Typography>
    </Box>
  )
}

function FieldSourceMap({ notes }: { notes: SourceTruthFieldNote[] }) {
  if (notes.length === 0) return null

  return (
    <Box
      sx={{
        border: (theme) => `1px solid ${alpha(theme.palette.divider, 0.68)}`,
        borderRadius: 1,
        overflow: 'hidden',
      }}
    >
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: 'minmax(0, 1fr)', sm: 'minmax(0, 1fr) minmax(120px, 0.4fr)' },
          gap: 1,
          px: 1,
          py: 0.65,
          backgroundColor: (theme) => alpha(theme.palette.background.default, 0.42),
          borderBottom: (theme) => `1px solid ${alpha(theme.palette.divider, 0.68)}`,
        }}
      >
        <FieldMetaLabel>Field path</FieldMetaLabel>
        <FieldMetaLabel>Source</FieldMetaLabel>
      </Box>
      <Box sx={{ maxHeight: 300, overflow: 'auto' }}>
        {notes.map((note) => (
          <Box
            key={`${note.objectLabel}/${note.fieldLabel}/${note.provider}`}
            sx={{
              display: 'grid',
              gridTemplateColumns: { xs: 'minmax(0, 1fr)', sm: 'minmax(0, 1fr) minmax(120px, 0.4fr)' },
              gap: 1,
              px: 1,
              py: 0.7,
              borderBottom: (theme) => `1px solid ${alpha(theme.palette.divider, 0.52)}`,
              '&:last-of-type': { borderBottom: 0 },
            }}
          >
            <Box sx={{ minWidth: 0 }}>
              <Typography variant="body2" sx={{ fontSize: '0.73rem', fontWeight: 650, lineHeight: 1.3 }}>
                {note.fieldLabel}
              </Typography>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ ...monoTextSx, display: 'block', mt: 0.15, fontSize: '0.64rem' }}
              >
                {note.objectLabel}
              </Typography>
            </Box>
            <Box sx={{ minWidth: 0, alignSelf: 'center' }}>
              <Chip size="small" variant="outlined" label={note.provider} sx={compactChipSx} />
            </Box>
          </Box>
        ))}
      </Box>
    </Box>
  )
}

function ValidationChips({ attachments }: { attachments: ValidationAttachmentView[] }) {
  if (attachments.length === 0) return null

  const counts = validationStateCounts(attachments)
  return (
    <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
      {counts.enabled > 0 && (
        <Chip size="small" color="success" variant="outlined" label={`${counts.enabled} active`} sx={compactChipSx} />
      )}
      {counts.active > 0 && counts.enabled === 0 && (
        <Chip size="small" color="success" variant="outlined" label={`${counts.active} active`} sx={compactChipSx} />
      )}
      {counts.optedOut > 0 && (
        <Chip size="small" color="warning" variant="outlined" label={`${counts.optedOut} opted out`} sx={compactChipSx} />
      )}
      {counts.planned > 0 && (
        <Chip size="small" color="warning" variant="outlined" label={`${counts.planned} planned`} sx={compactChipSx} />
      )}
      {counts.blocked > 0 && (
        <Chip size="small" color="error" variant="outlined" label={`${counts.blocked} unavailable`} sx={compactChipSx} />
      )}
    </Stack>
  )
}

function validationAttachmentStateLabel(attachment: ValidationAttachmentView): string {
  if (attachment.state === 'blocked') return 'unavailable'
  return attachment.state
}

function validationAttachmentTargetLabel(attachment: ValidationAttachmentView): string {
  if (attachment.target_label) return attachment.target_label
  if (attachment.scope === 'pack') return 'All extracted data'
  if (attachment.scope === 'object') return attachment.object_type || 'Extracted object'
  if (attachment.scope === 'field') return attachment.field_path || 'Field'
  return 'Validation metadata'
}

function ValidationAttachmentRows({ attachments }: { attachments: ValidationAttachmentView[] }) {
  if (attachments.length === 0) return null

  return (
    <Box
      sx={{
        border: (theme) => `1px solid ${alpha(theme.palette.divider, 0.65)}`,
        borderRadius: 1,
        overflow: 'hidden',
        backgroundColor: (theme) => alpha(theme.palette.background.paper, 0.38),
      }}
    >
      {attachments.map((attachment) => (
        <Box
          key={attachment.attachment_id}
          sx={{
            display: 'grid',
            gridTemplateColumns: { xs: 'minmax(0, 1fr)', sm: '92px minmax(0, 1fr)' },
            gap: 0.85,
            px: 1,
            py: 0.8,
            borderBottom: (theme) => `1px solid ${alpha(theme.palette.divider, 0.5)}`,
            '&:last-of-type': { borderBottom: 0 },
          }}
        >
          <Box sx={{ minWidth: 0 }}>
            <Chip
              size="small"
              color={chipColorForState(attachment.state)}
              variant="outlined"
              label={validationAttachmentStateLabel(attachment)}
              sx={compactChipSx}
            />
          </Box>
          <Box sx={{ minWidth: 0 }}>
            <Typography variant="body2" sx={{ fontSize: '0.74rem', fontWeight: 700, lineHeight: 1.3 }}>
              {attachment.label || attachment.validator_id}
            </Typography>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: 'block', mt: 0.2, fontSize: '0.65rem', lineHeight: 1.3, textWrap: 'pretty' }}
            >
              {validationAttachmentTargetLabel(attachment)}
            </Typography>
            {attachment.description && (
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: 'block', mt: 0.25, fontSize: '0.64rem', lineHeight: 1.35, textWrap: 'pretty' }}
              >
                {attachment.description}
              </Typography>
            )}
          </Box>
        </Box>
      ))}
    </Box>
  )
}

function ProviderRefs({ refs }: { refs: Record<string, unknown> }) {
  const entries = Object.entries(refs)
  if (entries.length === 0) return null

  return (
    <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
      {entries.map(([providerKey, value]) => (
        <Tooltip key={providerKey} title={formatProviderRef(providerKey, value)}>
          <Chip size="small" variant="outlined" label={providerKey} sx={compactChipSx} />
        </Tooltip>
      ))}
    </Stack>
  )
}

function SummaryMetric({
  label,
  value,
  mono = false,
}: {
  label: string
  value: ReactNode
  mono?: boolean
}) {
  return (
    <Box
      sx={{
        minWidth: 0,
        borderTop: (theme) => `1px solid ${alpha(theme.palette.divider, 0.8)}`,
        pt: 0.75,
      }}
    >
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: 'block', fontSize: '0.62rem', textTransform: 'uppercase', letterSpacing: 0 }}
      >
        {label}
      </Typography>
      <Typography
        variant="body2"
        sx={{
          mt: 0.15,
          fontSize: '0.76rem',
          fontWeight: 650,
          ...(mono ? monoTextSx : {}),
        }}
      >
        {value}
      </Typography>
    </Box>
  )
}

function SectionAccordion({
  title,
  summary,
  defaultExpanded = false,
  children,
}: SectionAccordionProps) {
  return (
    <Accordion
      disableGutters
      defaultExpanded={defaultExpanded}
      sx={{
        backgroundColor: 'transparent',
        boxShadow: 'none',
        border: (theme) => `1px solid ${alpha(theme.palette.divider, 0.65)}`,
        borderRadius: 1,
        overflow: 'hidden',
        '&::before': { display: 'none' },
      }}
    >
      <AccordionSummary
        expandIcon={<ExpandMoreIcon fontSize="small" />}
        sx={{
          minHeight: 42,
          px: 1.25,
          '& .MuiAccordionSummary-content': { my: 0.75 },
        }}
      >
        <Box sx={{ minWidth: 0 }}>
          <Typography variant="body2" sx={{ fontWeight: 700, fontSize: '0.78rem' }}>
            {title}
          </Typography>
          {summary && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: 'block', fontSize: '0.68rem', lineHeight: 1.25 }}
            >
              {summary}
            </Typography>
          )}
        </Box>
      </AccordionSummary>
      <AccordionDetails sx={{ px: 1.25, pt: 0, pb: 1.25 }}>
        {children}
      </AccordionDetails>
    </Accordion>
  )
}

function FieldMetaLabel({ children }: { children: ReactNode }) {
  return (
    <Typography
      variant="caption"
      color="text.secondary"
      sx={{ display: 'block', fontSize: '0.58rem', lineHeight: 1.2, textTransform: 'uppercase', letterSpacing: 0 }}
    >
      {children}
    </Typography>
  )
}

function FieldRow({ field, compact = false }: { field: DomainEnvelopeFieldMetadata; compact?: boolean }) {
  const label = field.display_name || field.field_path
  const schemaDetails = [
    field.enum_ref ? `choice list: ${field.enum_ref}` : null,
    field.object_type_ref ? `linked object: ${field.object_type_ref}` : null,
  ].filter(Boolean).join(' / ')

  if (compact) {
    return (
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: {
            xs: 'minmax(0, 1fr)',
            sm: 'minmax(180px, 1.35fr) minmax(120px, 0.7fr) minmax(150px, 0.9fr) minmax(150px, 0.85fr)',
          },
          gap: { xs: 0.75, sm: 1.25 },
          py: 0.95,
          borderBottom: (theme) => `1px solid ${alpha(theme.palette.divider, 0.6)}`,
          '&:last-of-type': { borderBottom: 0 },
        }}
      >
        <Box sx={{ minWidth: 0 }}>
          <Typography variant="body2" sx={{ fontSize: '0.76rem', fontWeight: 700, lineHeight: 1.25 }}>
            {label}
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ ...monoTextSx, display: 'block', mt: 0.2 }}>
            {field.field_path}
          </Typography>
          {field.description && (
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.35, lineHeight: 1.35 }}>
              {field.description}
            </Typography>
          )}
        </Box>

        <Box sx={{ minWidth: 0 }}>
          <FieldMetaLabel>Type</FieldMetaLabel>
          <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap sx={{ mt: 0.35 }}>
            <Chip size="small" variant="outlined" label={field.field_type} sx={compactChipSx} />
            {field.required && (
              <Chip size="small" color="primary" variant="outlined" label="required" sx={compactChipSx} />
            )}
          </Stack>
          {schemaDetails && (
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.35, lineHeight: 1.35 }}>
              {schemaDetails}
            </Typography>
          )}
        </Box>

        <Box sx={{ minWidth: 0 }}>
          <FieldMetaLabel>Source</FieldMetaLabel>
          <Typography variant="caption" color="text.secondary" sx={{ ...monoTextSx, display: 'block', mt: 0.35 }}>
            {field.source_of_truth || 'metadata'}
          </Typography>
          <Box sx={{ mt: 0.45 }}>
            <ProviderRefs refs={field.provider_refs} />
          </Box>
        </Box>

        <Box sx={{ minWidth: 0 }}>
          <FieldMetaLabel>Validation</FieldMetaLabel>
          <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap sx={{ mt: 0.35 }}>
            {field.definition_state !== 'stable' && (
              <Chip
                size="small"
                color={chipColorForState(field.definition_state)}
                variant="outlined"
                label={humanizeState(field.definition_state)}
                sx={compactChipSx}
              />
            )}
            <ValidationChips attachments={field.validation_attachments} />
          </Stack>
        </Box>
      </Box>
    )
  }

  return (
    <Box
      sx={{
        py: 0.75,
        px: 1,
        borderRadius: 1,
        border: (theme) => `1px solid ${alpha(theme.palette.divider, 0.6)}`,
        backgroundColor: (theme) => alpha(theme.palette.background.default, 0.25),
      }}
    >
      <Stack
        direction={{ xs: 'column', sm: compact ? 'row' : 'row' }}
        alignItems="flex-start"
        spacing={1}
      >
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Stack direction="row" spacing={0.75} alignItems="center" flexWrap="wrap" useFlexGap>
            <Typography variant="body2" sx={{ fontSize: '0.75rem', fontWeight: 600 }}>
              {label}
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={monoTextSx}>
              {field.field_path}
            </Typography>
            <Chip size="small" variant="outlined" label={field.field_type} sx={compactChipSx} />
            {field.required && (
              <Chip size="small" color="primary" variant="outlined" label="required" sx={compactChipSx} />
            )}
            {field.source_of_truth && (
              <Chip size="small" color="success" variant="outlined" label={`truth: ${field.source_of_truth}`} sx={compactChipSx} />
            )}
            {field.definition_state !== 'stable' && (
              <Chip
                size="small"
                color={chipColorForState(field.definition_state)}
                variant="outlined"
                label={humanizeState(field.definition_state)}
                sx={compactChipSx}
              />
            )}
          </Stack>
          {field.description && (
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.25 }}>
              {field.description}
            </Typography>
          )}
          {schemaDetails && (
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.25 }}>
              {schemaDetails}
            </Typography>
          )}
          <Box sx={{ mt: 0.5 }}>
            <ProviderRefs refs={field.provider_refs} />
          </Box>
        </Box>
        <ValidationChips attachments={field.validation_attachments} />
      </Stack>
    </Box>
  )
}

function ObjectPanel({
  object,
  defaultExpanded = true,
  fieldsDefaultExpanded = true,
  compact = false,
}: {
  object: DomainEnvelopeObjectMetadata
  defaultExpanded?: boolean
  fieldsDefaultExpanded?: boolean
  compact?: boolean
}) {
  const schemaLabel = formatSchemaRef(object.schema_ref)

  return (
    <Accordion
      disableGutters
      defaultExpanded={defaultExpanded}
      sx={{
        backgroundColor: 'transparent',
        boxShadow: 'none',
        border: (theme) => `1px solid ${alpha(theme.palette.divider, 0.65)}`,
        borderRadius: 1,
        overflow: 'hidden',
        '&::before': { display: 'none' },
      }}
    >
      <AccordionSummary
        expandIcon={<ExpandMoreIcon fontSize="small" />}
        sx={{
          minHeight: compact ? 48 : 38,
          px: compact ? 1.25 : 2,
          '& .MuiAccordionSummary-content': { minWidth: 0, my: compact ? 0.9 : 1 },
        }}
      >
        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: { xs: 'minmax(0, 1fr)', sm: compact ? 'minmax(0, 1fr) auto' : 'minmax(0, 1fr)' },
            gap: 1,
            alignItems: 'center',
            width: '100%',
            minWidth: 0,
          }}
        >
          <Box sx={{ minWidth: 0 }}>
            <Typography variant="body2" sx={{ fontWeight: 700, fontSize: compact ? '0.82rem' : '0.8rem', lineHeight: 1.25 }}>
              {object.display_name}
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={{ ...monoTextSx, display: 'block', mt: 0.2 }}>
              {object.object_type}
            </Typography>
          </Box>
          <Stack direction="row" spacing={0.5} alignItems="center" flexWrap="wrap" useFlexGap>
            <Chip size="small" variant="outlined" label={`${object.fields.length} fields`} sx={compactChipSx} />
            {object.object_role && (
              <Chip size="small" variant="outlined" label={object.object_role} sx={compactChipSx} />
            )}
            {object.definition_state !== 'stable' && (
              <Chip
                size="small"
                color={chipColorForState(object.definition_state)}
                variant="outlined"
                label={humanizeState(object.definition_state)}
                sx={compactChipSx}
              />
            )}
          </Stack>
        </Box>
      </AccordionSummary>
      <AccordionDetails sx={{ px: compact ? 1.25 : 2, pt: 0, pb: compact ? 1.25 : 2 }}>
        <Stack spacing={compact ? 1.25 : 1}>
          {object.description && (
            <Typography variant="caption" color="text.secondary" sx={{ lineHeight: 1.45 }}>
              {object.description}
            </Typography>
          )}
          {schemaLabel && (
            <Box
              sx={{
                display: 'grid',
                gridTemplateColumns: { xs: '1fr', sm: '88px minmax(0, 1fr)' },
                gap: 0.75,
                alignItems: 'baseline',
              }}
            >
              <FieldMetaLabel>Schema</FieldMetaLabel>
              <Typography variant="caption" color="text.secondary" sx={{ ...monoTextSx, lineHeight: 1.4 }}>
                {schemaLabel}
              </Typography>
            </Box>
          )}
          <ProviderRefs refs={object.provider_refs} />
          {object.validation_attachments.length > 0 && (
            <Box>
              <Stack direction="row" spacing={0.75} alignItems="center" flexWrap="wrap" useFlexGap sx={{ mb: 0.55 }}>
                <FieldMetaLabel>Object validation</FieldMetaLabel>
                <ValidationChips attachments={object.validation_attachments} />
              </Stack>
              <ValidationAttachmentRows attachments={object.validation_attachments} />
            </Box>
          )}
          <SectionAccordion
            title={`Fields (${object.fields.length})`}
            summary={compact ? 'Open for LinkML/database-backed field paths and validation metadata.' : undefined}
            defaultExpanded={fieldsDefaultExpanded}
          >
            <Box
              sx={{
                display: 'flex',
                flexDirection: 'column',
                maxHeight: compact ? 520 : 320,
                overflow: 'auto',
                borderTop: compact ? (theme) => `1px solid ${alpha(theme.palette.divider, 0.7)}` : 0,
                borderBottom: compact ? (theme) => `1px solid ${alpha(theme.palette.divider, 0.7)}` : 0,
              }}
            >
              {object.fields.map((field) => (
                <FieldRow key={field.field_path} field={field} compact={compact} />
              ))}
            </Box>
          </SectionAccordion>
        </Stack>
      </AccordionDetails>
    </Accordion>
  )
}

function DomainEnvelopeMetadataPanel({
  metadata,
  validationAttachments,
  compact = false,
  layout = 'standard',
  title = 'Domain Envelope',
  validationModeNote,
}: DomainEnvelopeMetadataPanelProps) {
  if (!metadata) return null

  const attachmentView = validationAttachments ?? metadata.validation_attachments
  const isFlowEditor = layout === 'flow-editor'
  const fieldCount = metadata.object_definitions.reduce(
    (count, object) => count + object.fields.length,
    0
  )
  const sourceTruthNotes = splitSourceTruthNotes(metadata.source_of_truth_notes.slice(1))
  const guidanceNoteCount = (
    sourceTruthNotes.objectNotes.length
    + sourceTruthNotes.otherNotes.length
    + (validationModeNote ? 1 : 0)
    + 1
  )

  return (
    <Box
      data-testid="domain-envelope-metadata-panel"
      sx={{
        border: (theme) => `1px solid ${alpha(theme.palette.divider, 0.75)}`,
        borderRadius: 1,
        p: isFlowEditor ? 1.25 : compact ? 1 : 1.5,
        backgroundColor: (theme) => alpha(theme.palette.background.default, 0.28),
        overflow: 'hidden',
      }}
    >
      <Stack spacing={compact ? 1 : 1.5}>
        {isFlowEditor ? (
          <Box
            sx={{
              p: 1,
              borderRadius: 1,
              backgroundColor: (theme) => alpha(theme.palette.background.paper, 0.58),
              border: (theme) => `1px solid ${alpha(theme.palette.divider, 0.65)}`,
            }}
          >
            <Box
              sx={{
                display: 'grid',
                gridTemplateColumns: { xs: 'minmax(0, 1fr)', md: 'minmax(0, 1fr) auto' },
                gap: 1,
                alignItems: 'start',
              }}
            >
              <Box sx={{ minWidth: 0 }}>
                <Typography variant="subtitle2" sx={{ fontSize: '0.88rem', fontWeight: 750, lineHeight: 1.25 }}>
                  {title}
                </Typography>
                <Typography variant="body2" sx={{ mt: 0.35, fontSize: '0.78rem', lineHeight: 1.35 }}>
                  {metadata.display_name}
                </Typography>
              </Box>
              <Chip
                size="small"
                color={chipColorForState(metadata.status)}
                variant="outlined"
                label={humanizeState(metadata.status)}
                sx={compactChipSx}
              />
            </Box>

            <Box
              sx={{
                display: 'grid',
                gridTemplateColumns: {
                  xs: 'repeat(2, minmax(0, 1fr))',
                  md: '1.4fr 0.7fr 0.9fr 1fr',
                },
                gap: 1,
                mt: 1,
              }}
            >
              <SummaryMetric label="Domain pack" value={metadata.domain_pack_id} mono />
              <SummaryMetric label="Version" value={`v${metadata.domain_pack_version}`} mono />
              <SummaryMetric
                label="Objects"
                value={`${metadata.object_definitions.length} types / ${fieldCount} fields`}
              />
              <SummaryMetric
                label="Validators"
                value={`${metadata.validation_summary.default_enabled} default`}
              />
            </Box>

            {(metadata.validation_summary.export_blocking > 0
              || metadata.validation_summary.opt_out_allowed > 0
              || attachmentView.length > 0) && (
              <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap sx={{ mt: 1 }}>
                {metadata.validation_summary.export_blocking > 0 && (
                  <Chip size="small" color="error" variant="outlined" label={`${metadata.validation_summary.export_blocking} required for export`} sx={compactChipSx} />
                )}
                {metadata.validation_summary.opt_out_allowed > 0 && (
                  <Chip size="small" color="warning" variant="outlined" label={`${metadata.validation_summary.opt_out_allowed} opt-out allowed`} sx={compactChipSx} />
                )}
                <ValidationChips attachments={attachmentView} />
              </Stack>
            )}
          </Box>
        ) : (
          <>
            <Box>
              <Stack direction="row" alignItems="center" spacing={0.75} flexWrap="wrap" useFlexGap>
                <Typography variant="subtitle2" sx={{ fontSize: compact ? '0.8rem' : '0.9rem', fontWeight: 700 }}>
                  {title}
                </Typography>
                <Chip size="small" variant="outlined" label={metadata.domain_pack_id} sx={{ ...compactChipSx, height: 20, fontSize: '0.65rem' }} />
                <Chip size="small" variant="outlined" label={`v${metadata.domain_pack_version}`} sx={{ ...compactChipSx, height: 20, fontSize: '0.65rem' }} />
                <Chip
                  size="small"
                  color={chipColorForState(metadata.status)}
                  variant="outlined"
                  label={humanizeState(metadata.status)}
                  sx={{ ...compactChipSx, height: 20, fontSize: '0.65rem' }}
                />
              </Stack>
              <Typography variant="body2" sx={{ mt: 0.5, fontSize: compact ? '0.75rem' : '0.8rem' }}>
                {metadata.display_name}
              </Typography>
              {metadata.description && !compact && (
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.25 }}>
                  {metadata.description}
                </Typography>
              )}
            </Box>

            <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
              <Chip size="small" label={`${metadata.object_definitions.length} object type${metadata.object_definitions.length === 1 ? '' : 's'}`} />
              <Chip size="small" label={`${metadata.validation_summary.default_enabled} default validator${metadata.validation_summary.default_enabled === 1 ? '' : 's'}`} />
              {metadata.validation_summary.export_blocking > 0 && (
                <Chip size="small" color="error" variant="outlined" label={`${metadata.validation_summary.export_blocking} required for export`} />
              )}
              {metadata.validation_summary.opt_out_allowed > 0 && (
                <Chip size="small" color="warning" variant="outlined" label={`${metadata.validation_summary.opt_out_allowed} opt-out allowed`} />
              )}
            </Stack>

            <ValidationChips attachments={attachmentView} />
          </>
        )}

        {isFlowEditor ? (
          <>
            <SectionAccordion
              title="Guidance"
              summary={`${guidanceNoteCount} workflow note${guidanceNoteCount === 1 ? '' : 's'}; field source map is separated below.`}
              defaultExpanded
            >
              <Stack spacing={1}>
                <GuidanceCard label="Source of truth" tone="info">
                  {metadata.semantic_source_note}
                </GuidanceCard>

                {validationModeNote && (
                  <GuidanceCard label="Automatic validation" tone="success">
                    {validationModeNote}
                  </GuidanceCard>
                )}

                {sourceTruthNotes.objectNotes.length > 0 && (
                  <GuidanceCard label="Object shape">
                    <Stack component="ul" spacing={0.5} sx={{ m: 0, pl: 2 }}>
                      {sourceTruthNotes.objectNotes.map((note) => (
                        <Typography key={note} component="li" variant="body2" color="text.secondary" sx={{ fontSize: '0.75rem', lineHeight: 1.45 }}>
                          {note}
                        </Typography>
                      ))}
                    </Stack>
                  </GuidanceCard>
                )}

                {sourceTruthNotes.otherNotes.length > 0 && (
                  <GuidanceCard label="Notes">
                    <Stack spacing={0.5}>
                      {sourceTruthNotes.otherNotes.map((note) => (
                        <Typography key={note} variant="body2" color="text.secondary" sx={{ fontSize: '0.75rem', lineHeight: 1.45 }}>
                        {note}
                      </Typography>
                    ))}
                  </Stack>
                  </GuidanceCard>
                )}
              </Stack>
            </SectionAccordion>

            {sourceTruthNotes.fieldNotes.length > 0 && (
              <SectionAccordion
                title="Field source map"
                summary={`${sourceTruthNotes.fieldNotes.length} LinkML/database source mapping${sourceTruthNotes.fieldNotes.length === 1 ? '' : 's'}; open only when you need field-level detail.`}
                defaultExpanded={false}
              >
                <FieldSourceMap notes={sourceTruthNotes.fieldNotes} />
              </SectionAccordion>
            )}

            <SectionAccordion
              title="Schema References"
              summary={`${metadata.schema_refs.length} schema reference${metadata.schema_refs.length === 1 ? '' : 's'} and ${Object.keys(metadata.provider_refs).length} provider reference${Object.keys(metadata.provider_refs).length === 1 ? '' : 's'}.`}
            >
              <Stack spacing={1}>
                {metadata.schema_refs.length > 0 && (
                  <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
                    {metadata.schema_refs.map((schemaRef) => (
                      <Tooltip key={schemaRef.schema_id} title={schemaRef.uri || formatSchemaRef(schemaRef)}>
                        <Chip size="small" variant="outlined" label={formatSchemaRef(schemaRef)} />
                      </Tooltip>
                    ))}
                  </Stack>
                )}
                <ProviderRefs refs={metadata.provider_refs} />
              </Stack>
            </SectionAccordion>

            <Stack spacing={1}>
              {metadata.object_definitions.map((object) => (
                <ObjectPanel
                  key={object.object_type}
                  object={object}
                  defaultExpanded={false}
                  fieldsDefaultExpanded={false}
                  compact
                />
              ))}
            </Stack>
          </>
        ) : (
          <>
            <Alert severity="info" sx={{ py: 0.5, '& .MuiAlert-message': { fontSize: '0.75rem' } }}>
              {metadata.semantic_source_note}
            </Alert>

            {validationModeNote && (
              <Alert severity="success" sx={{ py: 0.5, '& .MuiAlert-message': { fontSize: '0.75rem' } }}>
                {validationModeNote}
              </Alert>
            )}

            {metadata.schema_refs.length > 0 && (
              <Box>
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
                  Schema references
                </Typography>
                <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
                  {metadata.schema_refs.map((schemaRef) => (
                    <Tooltip key={schemaRef.schema_id} title={schemaRef.uri || formatSchemaRef(schemaRef)}>
                      <Chip size="small" variant="outlined" label={formatSchemaRef(schemaRef)} />
                    </Tooltip>
                  ))}
                </Stack>
              </Box>
            )}

            <ProviderRefs refs={metadata.provider_refs} />

            {metadata.source_of_truth_notes.length > 1 && (
              <Box>
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
                  Source-of-truth notes
                </Typography>
                <Stack spacing={0.25}>
                  {metadata.source_of_truth_notes.slice(1, compact ? 3 : 6).map((note) => (
                    <Typography key={note} variant="caption" color="text.secondary">
                      {note}
                    </Typography>
                  ))}
                </Stack>
              </Box>
            )}

            <Divider />

            <Stack spacing={1}>
              {metadata.object_definitions.map((object) => (
                <ObjectPanel key={object.object_type} object={object} />
              ))}
            </Stack>
          </>
        )}
      </Stack>
    </Box>
  )
}

export default DomainEnvelopeMetadataPanel
