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
  title?: string
  validationModeNote?: string
}

function humanizeState(value: string): string {
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

function ValidationChips({ attachments }: { attachments: ValidationAttachmentView[] }) {
  if (attachments.length === 0) return null

  const counts = validationStateCounts(attachments)
  return (
    <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
      {counts.enabled > 0 && (
        <Chip size="small" color="success" variant="outlined" label={`${counts.enabled} auto`} />
      )}
      {counts.active > 0 && counts.enabled === 0 && (
        <Chip size="small" color="success" variant="outlined" label={`${counts.active} active`} />
      )}
      {counts.optedOut > 0 && (
        <Chip size="small" color="warning" variant="outlined" label={`${counts.optedOut} opted out`} />
      )}
      {counts.planned > 0 && (
        <Chip size="small" color="warning" variant="outlined" label={`${counts.planned} planned`} />
      )}
      {counts.blocked > 0 && (
        <Chip size="small" color="error" variant="outlined" label={`${counts.blocked} blocked`} />
      )}
    </Stack>
  )
}

function ProviderRefs({ refs }: { refs: Record<string, unknown> }) {
  const entries = Object.entries(refs)
  if (entries.length === 0) return null

  return (
    <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
      {entries.map(([providerKey, value]) => (
        <Tooltip key={providerKey} title={formatProviderRef(providerKey, value)}>
          <Chip size="small" variant="outlined" label={providerKey} />
        </Tooltip>
      ))}
    </Stack>
  )
}

function FieldRow({ field }: { field: DomainEnvelopeFieldMetadata }) {
  const label = field.display_name || field.field_path
  const schemaDetails = [
    field.enum_ref ? `enum ${field.enum_ref}` : null,
    field.model_ref ? `model ${field.model_ref}` : null,
    field.object_type_ref ? `object ${field.object_type_ref}` : null,
  ].filter(Boolean).join(' / ')

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
      <Stack direction="row" alignItems="flex-start" spacing={1}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Stack direction="row" spacing={0.75} alignItems="center" flexWrap="wrap" useFlexGap>
            <Typography variant="body2" sx={{ fontSize: '0.75rem', fontWeight: 600 }}>
              {label}
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={{ fontFamily: 'monospace' }}>
              {field.field_path}
            </Typography>
            <Chip size="small" variant="outlined" label={field.field_type} sx={{ height: 18, fontSize: '0.6rem' }} />
            {field.required && (
              <Chip size="small" color="primary" variant="outlined" label="required" sx={{ height: 18, fontSize: '0.6rem' }} />
            )}
            {field.source_of_truth && (
              <Chip size="small" color="success" variant="outlined" label={`truth: ${field.source_of_truth}`} sx={{ height: 18, fontSize: '0.6rem' }} />
            )}
            {field.definition_state !== 'stable' && (
              <Chip
                size="small"
                color={chipColorForState(field.definition_state)}
                variant="outlined"
                label={humanizeState(field.definition_state)}
                sx={{ height: 18, fontSize: '0.6rem' }}
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

function ObjectPanel({ object }: { object: DomainEnvelopeObjectMetadata }) {
  const schemaLabel = formatSchemaRef(object.schema_ref)

  return (
    <Accordion
      disableGutters
      defaultExpanded
      sx={{
        backgroundColor: 'transparent',
        boxShadow: 'none',
        border: (theme) => `1px solid ${alpha(theme.palette.divider, 0.65)}`,
        '&::before': { display: 'none' },
      }}
    >
      <AccordionSummary expandIcon={<ExpandMoreIcon fontSize="small" />} sx={{ minHeight: 38 }}>
        <Stack direction="row" spacing={0.75} alignItems="center" flexWrap="wrap" useFlexGap>
          <Typography variant="body2" sx={{ fontWeight: 600, fontSize: '0.8rem' }}>
            {object.display_name}
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ fontFamily: 'monospace' }}>
            {object.object_type}
          </Typography>
          {object.object_role && (
            <Chip size="small" variant="outlined" label={object.object_role} sx={{ height: 18, fontSize: '0.6rem' }} />
          )}
          {object.definition_state !== 'stable' && (
            <Chip
              size="small"
              color={chipColorForState(object.definition_state)}
              variant="outlined"
              label={humanizeState(object.definition_state)}
              sx={{ height: 18, fontSize: '0.6rem' }}
            />
          )}
        </Stack>
      </AccordionSummary>
      <AccordionDetails sx={{ pt: 0 }}>
        <Stack spacing={1}>
          {object.description && (
            <Typography variant="caption" color="text.secondary">
              {object.description}
            </Typography>
          )}
          {schemaLabel && (
            <Typography variant="caption" color="text.secondary">
              Schema: {schemaLabel}
            </Typography>
          )}
          <ProviderRefs refs={object.provider_refs} />
          {object.validation_attachments.length > 0 && (
            <ValidationChips attachments={object.validation_attachments} />
          )}
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75, maxHeight: 320, overflow: 'auto' }}>
            {object.fields.map((field) => (
              <FieldRow key={field.field_path} field={field} />
            ))}
          </Box>
        </Stack>
      </AccordionDetails>
    </Accordion>
  )
}

function DomainEnvelopeMetadataPanel({
  metadata,
  validationAttachments,
  compact = false,
  title = 'Domain Envelope',
  validationModeNote,
}: DomainEnvelopeMetadataPanelProps) {
  if (!metadata) return null

  const attachmentView = validationAttachments ?? metadata.validation_attachments

  return (
    <Box
      data-testid="domain-envelope-metadata-panel"
      sx={{
        border: (theme) => `1px solid ${alpha(theme.palette.divider, 0.75)}`,
        borderRadius: 1,
        p: compact ? 1 : 1.5,
        backgroundColor: (theme) => alpha(theme.palette.background.default, 0.28),
      }}
    >
      <Stack spacing={compact ? 1 : 1.5}>
        <Box>
          <Stack direction="row" alignItems="center" spacing={0.75} flexWrap="wrap" useFlexGap>
            <Typography variant="subtitle2" sx={{ fontSize: compact ? '0.8rem' : '0.9rem', fontWeight: 700 }}>
              {title}
            </Typography>
            <Chip size="small" variant="outlined" label={metadata.domain_pack_id} sx={{ height: 20, fontSize: '0.65rem' }} />
            <Chip size="small" variant="outlined" label={`v${metadata.domain_pack_version}`} sx={{ height: 20, fontSize: '0.65rem' }} />
            <Chip
              size="small"
              color={chipColorForState(metadata.status)}
              variant="outlined"
              label={humanizeState(metadata.status)}
              sx={{ height: 20, fontSize: '0.65rem' }}
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

        <Alert severity="info" sx={{ py: 0.5, '& .MuiAlert-message': { fontSize: '0.75rem' } }}>
          {metadata.semantic_source_note}
        </Alert>

        {validationModeNote && (
          <Alert severity="success" sx={{ py: 0.5, '& .MuiAlert-message': { fontSize: '0.75rem' } }}>
            {validationModeNote}
          </Alert>
        )}

        <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
          <Chip size="small" label={`${metadata.object_definitions.length} object type${metadata.object_definitions.length === 1 ? '' : 's'}`} />
          <Chip size="small" label={`${metadata.validation_summary.default_enabled} default validator${metadata.validation_summary.default_enabled === 1 ? '' : 's'}`} />
          {metadata.validation_summary.export_blocking > 0 && (
            <Chip size="small" color="error" variant="outlined" label={`${metadata.validation_summary.export_blocking} export-blocking`} />
          )}
          {metadata.validation_summary.opt_out_allowed > 0 && (
            <Chip size="small" color="warning" variant="outlined" label={`${metadata.validation_summary.opt_out_allowed} opt-out allowed`} />
          )}
        </Stack>

        <ValidationChips attachments={attachmentView} />

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
      </Stack>
    </Box>
  )
}

export default DomainEnvelopeMetadataPanel
