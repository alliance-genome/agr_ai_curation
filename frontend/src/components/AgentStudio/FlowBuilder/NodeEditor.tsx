/**
 * NodeEditor Component
 *
 * Configuration panel for editing a selected flow node.
 * Allows setting step goal, custom instructions, output options, and artifact identifiers.
 */

import { useState, useEffect } from 'react'
import {
  Box,
  Typography,
  TextField,
  Paper,
  IconButton,
  Checkbox,
  FormControlLabel,
  Button,
  Divider,
  Tooltip,
  Alert,
  Chip,
} from '@mui/material'
import { styled, alpha } from '@mui/material/styles'
import CloseIcon from '@mui/icons-material/Close'
import SaveIcon from '@mui/icons-material/Save'
import DeleteIcon from '@mui/icons-material/Delete'
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined'
import DescriptionIcon from '@mui/icons-material/Description'
import SchemaIcon from '@mui/icons-material/Schema'

import { validationAttachmentForPersistence } from './types'
import type {
  NodeEditorProps,
  ValidationAttachmentGroup,
  ValidationAttachmentSelection,
} from './types'
import {
  isValidationAgentFromMetadata,
  isOutputFormatterAgentFromMetadata,
  resolveOutputFormatterIncludeEvidence,
} from './agentMetadataUtils'
import { useAgentMetadata } from '@/contexts/AgentMetadataContext'
import { useAgentIcon } from '@/hooks/useAgentIcon'

const EditorContainer = styled(Paper)(({ theme }) => ({
  position: 'absolute',
  top: 0,
  right: 0,
  bottom: 0,
  width: '100%',
  maxWidth: 420,
  backgroundColor: theme.palette.background.paper,
  borderLeft: `1px solid ${theme.palette.divider}`,
  display: 'flex',
  flexDirection: 'column',
  zIndex: 10,
  overflow: 'hidden',
  boxShadow: theme.shadows[8],
  [theme.breakpoints.down('md')]: {
    width: '100%',
    maxWidth: '100%',
  },
}))

const EditorHeader = styled(Box)(({ theme }) => ({
  padding: theme.spacing(1.5),
  borderBottom: `1px solid ${theme.palette.divider}`,
  display: 'flex',
  alignItems: 'center',
  gap: theme.spacing(1),
  backgroundColor: alpha(theme.palette.primary.main, 0.05),
}))

const EditorContent = styled(Box)(({ theme }) => ({
  flex: 1,
  overflow: 'auto',
  padding: theme.spacing(2),
  display: 'flex',
  flexDirection: 'column',
  gap: theme.spacing(2),
}))

const EditorFooter = styled(Box)(({ theme }) => ({
  padding: theme.spacing(1.5),
  borderTop: `1px solid ${theme.palette.divider}`,
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
}))

const IconWrapper = styled(Box)(({ theme }) => ({
  fontSize: '1.5rem',
  width: 32,
  height: 32,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  borderRadius: theme.shape.borderRadius,
  backgroundColor: alpha(theme.palette.primary.main, 0.1),
}))

const FieldLabel = styled(Box)(({ theme }) => ({
  display: 'flex',
  alignItems: 'center',
  gap: theme.spacing(0.5),
  marginBottom: theme.spacing(0.5),
}))

const VariableChip = styled(Box)(({ theme }) => ({
  display: 'inline-block',
  padding: theme.spacing(0.25, 0.75),
  borderRadius: theme.shape.borderRadius,
  backgroundColor: alpha(theme.palette.info.main, 0.1),
  color: theme.palette.info.main,
  fontSize: '0.7rem',
  fontFamily: 'monospace',
  marginRight: theme.spacing(0.5),
  marginBottom: theme.spacing(0.5),
  cursor: 'pointer',
  '&:hover': {
    backgroundColor: alpha(theme.palette.info.main, 0.2),
  },
}))

const BUILT_IN_TEMPLATE_VARIABLES = [
  'input_filename',
  'input_filename_stem',
  'trace_id',
  'timestamp',
] as const

const validationStateLabel = (state: ValidationAttachmentSelection['state']) => {
  if (state === 'under_development') return 'under development'
  return state
}

const validationStateHelpText = (attachment: ValidationAttachmentSelection) => {
  if (attachment.state === 'under_development') {
    return attachment.state_explanation?.trim()
      ? attachment.state_explanation
      : 'Missing under-development state explanation'
  }
  return ''
}

const validationTargetText = (attachment: ValidationAttachmentSelection) => {
  if (attachment.target_label) return attachment.target_label
  if (attachment.scope === 'pack') return 'All extracted data'
  if (attachment.object_type && attachment.field_path) return 'Specific field'
  if (attachment.object_type) return 'Specific extracted object'
  return 'Validation metadata'
}

const validationOwnerText = (attachment: ValidationAttachmentSelection) => {
  const owner = attachment.validator_package_id && attachment.validator_agent_id
    ? `${attachment.validator_package_id}:${attachment.validator_agent_id}`
    : attachment.validator_id
  return `${attachment.domain_pack_id}${attachment.domain_pack_version ? ` v${attachment.domain_pack_version}` : ''} / ${owner}`
}

const validationGroupStateLabel = (
  attachment: ValidationAttachmentSelection,
  group?: ValidationAttachmentGroup
) => {
  if (group?.state === 'replaced') return 'custom replacement'
  if (group?.state === 'supplemental') return 'supplemental'
  if (attachment.state !== 'active') return validationStateLabel(attachment.state)
  return attachment.enabled ? 'automatic' : 'skipped'
}

const supplementalGroupLabel = (group: ValidationAttachmentGroup) => (
  group.label?.trim()
    || (group.binding_id ? `Supplemental validator: ${group.binding_id}` : 'Supplemental validator')
)

const supplementalGroupTargetText = (group: ValidationAttachmentGroup) => {
  if (group.replaces_attachment_id) return `Supplements attachment ${group.replaces_attachment_id}`
  if (group.binding_id) return `Binding ${group.binding_id}`
  return 'Validation attachment edge'
}

const supplementalGroupOwnerText = (group: ValidationAttachmentGroup) => {
  const parts = [
    group.validator_node_id ? `validator node ${group.validator_node_id}` : null,
    group.edge_id ? `edge ${group.edge_id}` : null,
  ].filter(Boolean)

  return parts.length > 0 ? parts.join(' / ') : 'Missing supplemental edge metadata'
}

function NodeEditor({
  node,
  outputBinding,
  onSave,
  onClose,
  onDelete,
  onViewPrompts,
  onViewDomainEnvelope,
}: NodeEditorProps) {
  const { agents: agentMetadata } = useAgentMetadata()

  // Form state
  const [customInstructions, setCustomInstructions] = useState('')
  const [includeEvidence, setIncludeEvidence] = useState(false)
  const [outputFilenameTemplate, setOutputFilenameTemplate] = useState('')
  const [outputKey, setOutputKey] = useState('')
  const [validationAttachments, setValidationAttachments] = useState<ValidationAttachmentSelection[]>([])

  const agentMetadataEntry = node ? agentMetadata[node.data.agent_id] : undefined
  const domainEnvelopeMetadata = agentMetadataEntry?.domain_envelope
  const isValidationAgentNode = node
    ? isValidationAgentFromMetadata(node.data.agent_id, agentMetadata)
    : false
  const supportsOutputFormatting = node
    ? isOutputFormatterAgentFromMetadata(node.data.agent_id, agentMetadata)
    : false
  const customInstructionsLabel = isValidationAgentNode
    ? 'Validation Steering Prompt (Optional)'
    : 'Custom Instructions (Optional)'
  const customInstructionsTooltip = isValidationAgentNode
    ? 'Use this prompt to focus a validation agent on a specific envelope object, field path, or curator concern. It is saved with this validator node.'
    : 'These instructions take the highest priority and override the agent\'s base prompt and group rules for this flow step. Use them to add constraints or focus the agent\'s behavior.'
  const actionableValidationAttachments = validationAttachments.filter(
    (attachment) => attachment.state === 'active' && Boolean(attachment.validator_binding_id)
  )
  const metadataValidationAttachments = validationAttachments.filter(
    (attachment) => !(attachment.state === 'active' && Boolean(attachment.validator_binding_id))
  )
  const supplementalValidationGroups = (node?.data.validation_groups || []).filter(
    (group) => group.state === 'supplemental'
      && !validationAttachments.some((attachment) => (
        (group.attachment_id && attachment.attachment_id === group.attachment_id)
        || (group.binding_id && attachment.validator_binding_id === group.binding_id)
      ))
  )

  // Initialize form when node changes
  useEffect(() => {
    if (node) {
      setCustomInstructions(node.data.custom_instructions || '')
      setIncludeEvidence(
        resolveOutputFormatterIncludeEvidence(
          node.data.agent_id,
          agentMetadata,
          node.data.include_evidence,
        ) ?? false
      )
      setOutputFilenameTemplate(node.data.output_filename_template || '')
      setOutputKey(node.data.output_key || `${node.data.agent_id}_output`)
      setValidationAttachments(node.data.validation_attachments || [])
    }
  }, [node, agentMetadata])

  // Handle save
  const handleSave = () => {
    if (!node) return

    const nextIncludeEvidence = agentMetadataEntry
      ? resolveOutputFormatterIncludeEvidence(
          node.data.agent_id,
          agentMetadata,
          includeEvidence,
        )
      : node.data.include_evidence

    onSave(node.id, {
      custom_instructions: customInstructions || undefined,
      include_evidence: nextIncludeEvidence,
      output_filename_template: supportsOutputFormatting
        ? outputFilenameTemplate.trim() || undefined
        : undefined,
      output_key: outputKey,
      validation_attachments: validationAttachments.length > 0
        ? validationAttachments.map(validationAttachmentForPersistence)
        : undefined,
    })

    onClose()
  }

  const handleInsertOutputFilenameVariable = (variable: string) => {
    setOutputFilenameTemplate((prev) => prev + `{{${variable}}}`)
  }

  const handleValidationToggle = (attachmentId: string, enabled: boolean) => {
    if (!node) return

    const group = node.data.validation_groups?.find(
      (candidate) => candidate.attachment_id === attachmentId
    )
    if (group?.state === 'replaced') return

    setValidationAttachments((current) => current.map((attachment) => (
      attachment.attachment_id === attachmentId
        ? { ...attachment, enabled }
        : attachment
    )))
  }

  // Get icon from registry via hook
  const icon = useAgentIcon(node?.data.agent_id)

  if (!node) return null

  return (
    <EditorContainer elevation={4}>
      <EditorHeader>
        <IconWrapper>{icon}</IconWrapper>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600, fontSize: '0.9rem' }}>
            {node.data.agent_display_name}
          </Typography>
          {node.data.agent_description && (
            <Typography
              variant="caption"
              sx={{
                display: 'block',
                color: 'text.secondary',
                fontSize: '0.7rem',
                lineHeight: 1.3,
                mt: 0.25,
              }}
            >
              {node.data.agent_description}
            </Typography>
          )}
        </Box>
        <IconButton size="small" onClick={onClose} sx={{ alignSelf: 'flex-start' }}>
          <CloseIcon fontSize="small" />
        </IconButton>
      </EditorHeader>

      <EditorContent>
        {/* Error Banner */}
        {node.data.hasError && (
          <Alert
            severity="error"
            sx={{
              py: 0.5,
              px: 1.5,
              fontSize: '0.75rem',
              '& .MuiAlert-message': { padding: 0 },
              '& .MuiAlert-icon': { mr: 1, py: 0 },
            }}
          >
            {node.data.errorMessage || 'Configuration error'}
          </Alert>
        )}

        {domainEnvelopeMetadata && (
          <Box
            sx={{
              border: (theme) => `1px solid ${alpha(theme.palette.divider, 0.75)}`,
              borderRadius: 1,
              p: 1.25,
              backgroundColor: (theme) => alpha(theme.palette.background.default, 0.28),
            }}
          >
            <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1 }}>
              <SchemaIcon sx={{ fontSize: 18, color: 'primary.main', mt: 0.2 }} />
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography variant="caption" sx={{ display: 'block', fontWeight: 700 }}>
                  Domain Envelope
                </Typography>
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.25, lineHeight: 1.35 }}>
                  {domainEnvelopeMetadata.display_name}
                </Typography>
                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, mt: 0.75 }}>
                  <Chip
                    size="small"
                    variant="outlined"
                    label={`${domainEnvelopeMetadata.object_definitions.length} object type${domainEnvelopeMetadata.object_definitions.length === 1 ? '' : 's'}`}
                    sx={{ height: 20, fontSize: '0.65rem' }}
                  />
                  <Chip
                    size="small"
                    variant="outlined"
                    label={`${domainEnvelopeMetadata.validation_summary.default_enabled} default validator${domainEnvelopeMetadata.validation_summary.default_enabled === 1 ? '' : 's'}`}
                    sx={{ height: 20, fontSize: '0.65rem' }}
                  />
                </Box>
              </Box>
            </Box>
            {onViewDomainEnvelope && (
              <Button
                size="small"
                variant="outlined"
                fullWidth
                onClick={() => onViewDomainEnvelope(node.id)}
                sx={{ mt: 1, justifyContent: 'flex-start' }}
                startIcon={<SchemaIcon fontSize="small" />}
              >
                View envelope details
              </Button>
            )}
          </Box>
        )}

        {isValidationAgentNode && (
          <Alert
            severity="info"
            sx={{
              py: 0.5,
              px: 1.5,
              fontSize: '0.75rem',
              '& .MuiAlert-message': { padding: 0 },
              '& .MuiAlert-icon': { mr: 1, py: 0 },
            }}
          >
            Custom validation agents attach to extraction steps as validation sidecars. Use the steering prompt to target the envelope object, field path, or validation question.
          </Alert>
        )}

        {/* View Prompts Link */}
        {onViewPrompts && (
          <Box
            onClick={() => onViewPrompts(node.data.agent_id, node.data.agent_display_name)}
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 0.75,
              cursor: 'pointer',
              color: 'primary.main',
              py: 1,
              px: 1.5,
              mx: -1,
              borderRadius: 1,
              backgroundColor: (theme) => alpha(theme.palette.primary.main, 0.08),
              '&:hover': {
                backgroundColor: (theme) => alpha(theme.palette.primary.main, 0.15),
              },
            }}
          >
            <DescriptionIcon sx={{ fontSize: 18 }} />
            <Typography variant="body2" sx={{ fontWeight: 500, fontSize: '0.8rem' }}>
              View base prompt & group rules
            </Typography>
          </Box>
        )}

        {/* Custom Instructions */}
        <Box>
          <FieldLabel>
            <Typography variant="caption" fontWeight={600}>
              {customInstructionsLabel}
            </Typography>
            <Tooltip title={customInstructionsTooltip}>
              <InfoOutlinedIcon sx={{ fontSize: 14, color: 'text.secondary' }} />
            </Tooltip>
          </FieldLabel>
          <TextField
            fullWidth
            size="small"
            placeholder="e.g., Focus on C. elegans genes. Include all isoforms."
            value={customInstructions}
            onChange={(e) => setCustomInstructions(e.target.value)}
            multiline
            rows={3}
          />
        </Box>

        <Divider />

        {(validationAttachments.length > 0 || supplementalValidationGroups.length > 0) && (
          <>
            <Box>
              <FieldLabel>
                <Typography variant="caption" fontWeight={600}>
                  Validation Attachments
                </Typography>
                <Tooltip title="Checked active validators run automatically. Under-development and metadata-only validators are shown below as read-only domain-pack context.">
                  <InfoOutlinedIcon sx={{ fontSize: 14, color: 'text.secondary' }} />
                </Tooltip>
              </FieldLabel>
              {actionableValidationAttachments.length > 0 && (
                <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
                  {actionableValidationAttachments.map((attachment) => {
                  const hasBinding = Boolean(attachment.validator_binding_id)
                  const group = node.data.validation_groups?.find(
                    (candidate) => candidate.attachment_id === attachment.attachment_id
                  )
                  const canToggle = attachment.state === 'active'
                    && hasBinding
                    && attachment.allow_opt_out
                    && group?.state !== 'replaced'
                  const stateColor: 'success' | 'warning' = attachment.state === 'active'
                    ? 'success'
                    : 'warning'
                  return (
                    <Box
                      key={attachment.attachment_id}
                      sx={{
                        border: (theme) => `1px solid ${theme.palette.divider}`,
                        borderRadius: 1,
                        p: 1,
                        backgroundColor: (theme) => alpha(theme.palette.background.default, 0.35),
                      }}
                    >
                      <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 0.75 }}>
                        <Checkbox
                          size="small"
                          checked={attachment.enabled}
                          disabled={!canToggle}
                          onChange={(event) => handleValidationToggle(
                            attachment.attachment_id,
                            event.target.checked
                          )}
                          sx={{ p: 0.25 }}
                        />
                        <Box sx={{ flex: 1, minWidth: 0 }}>
                          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, flexWrap: 'wrap' }}>
                            <Typography
                              variant="body2"
                              sx={{
                                flex: '1 1 180px',
                                minWidth: 0,
                                fontSize: '0.75rem',
                                fontWeight: 650,
                                lineHeight: 1.25,
                                overflowWrap: 'anywhere',
                                wordBreak: 'break-word',
                              }}
                            >
                              {attachment.label}
                            </Typography>
                            <Chip
                              size="small"
                              color={stateColor}
                              label={validationGroupStateLabel(attachment, group)}
                              sx={{ height: 18, fontSize: '0.6rem' }}
                            />
                            {attachment.required && (
                              <Chip
                                size="small"
                                color="primary"
                                variant="outlined"
                                label="required"
                                sx={{ height: 18, fontSize: '0.6rem' }}
                              />
                            )}
                            {attachment.blocking && (
                              <Chip
                                size="small"
                                color="error"
                                variant="outlined"
                                label="blocking"
                                sx={{ height: 18, fontSize: '0.6rem' }}
                              />
                            )}
                            {attachment.allow_opt_out && (
                              <Chip
                                size="small"
                                color="warning"
                                variant="outlined"
                                label="opt-out allowed"
                                sx={{ height: 18, fontSize: '0.6rem' }}
                              />
                            )}
                          </Box>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            sx={{
                              display: 'block',
                              fontSize: '0.65rem',
                              lineHeight: 1.3,
                              overflowWrap: 'anywhere',
                              wordBreak: 'break-word',
                            }}
                          >
                            {validationTargetText(attachment)}
                          </Typography>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            sx={{
                              display: 'block',
                              fontSize: '0.62rem',
                              lineHeight: 1.3,
                              overflowWrap: 'anywhere',
                              wordBreak: 'break-word',
                            }}
                          >
                            {validationOwnerText(attachment)}
                          </Typography>
                          {group?.state === 'replaced' && (
                            <Typography variant="caption" color="success.main" sx={{ display: 'block', fontSize: '0.65rem' }}>
                              Replaced by a custom validator edge
                            </Typography>
                          )}
                          {attachment.state === 'active' && !attachment.allow_opt_out && (
                            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', fontSize: '0.65rem' }}>
                              Locked by policy
                            </Typography>
                          )}
                          {!hasBinding && (
                            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', fontSize: '0.65rem' }}>
                              Metadata only
                            </Typography>
                          )}
                        </Box>
                      </Box>
                    </Box>
                  )
                })}
                </Box>
              )}

              {supplementalValidationGroups.length > 0 && (
                <Box sx={{ mt: actionableValidationAttachments.length > 0 ? 1.25 : 0 }}>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ display: 'block', mb: 0.5, fontSize: '0.65rem', lineHeight: 1.35 }}
                  >
                    Supplemental validators are custom validation edges that add checks outside the declared automatic bindings.
                  </Typography>
                  <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75 }}>
                    {supplementalValidationGroups.map((group) => (
                      <Box
                        key={group.group_id}
                        sx={{
                          border: (theme) => `1px solid ${alpha(theme.palette.info.main, 0.35)}`,
                          borderRadius: 1,
                          p: 0.85,
                          backgroundColor: (theme) => alpha(theme.palette.info.main, 0.08),
                        }}
                      >
                        <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 0.75 }}>
                          <Chip
                            size="small"
                            color="info"
                            variant="outlined"
                            label="supplemental"
                            sx={{ height: 18, fontSize: '0.6rem', mt: 0.1, flexShrink: 0 }}
                          />
                          <Box sx={{ flex: 1, minWidth: 0 }}>
                            <Typography
                              variant="body2"
                              sx={{
                                fontSize: '0.72rem',
                                fontWeight: 650,
                                lineHeight: 1.3,
                                overflowWrap: 'anywhere',
                                wordBreak: 'break-word',
                              }}
                            >
                              {supplementalGroupLabel(group)}
                            </Typography>
                            <Typography
                              variant="caption"
                              color="text.secondary"
                              sx={{
                                display: 'block',
                                mt: 0.2,
                                fontSize: '0.63rem',
                                lineHeight: 1.3,
                                overflowWrap: 'anywhere',
                                wordBreak: 'break-word',
                              }}
                            >
                              {supplementalGroupTargetText(group)}
                            </Typography>
                            <Typography
                              variant="caption"
                              color="text.secondary"
                              sx={{
                                display: 'block',
                                mt: 0.15,
                                fontSize: '0.61rem',
                                lineHeight: 1.3,
                                overflowWrap: 'anywhere',
                                wordBreak: 'break-word',
                              }}
                            >
                              {supplementalGroupOwnerText(group)}
                            </Typography>
                          </Box>
                        </Box>
                      </Box>
                    ))}
                  </Box>
                </Box>
              )}

              {metadataValidationAttachments.length > 0 && (
                <Box sx={{ mt: actionableValidationAttachments.length > 0 || supplementalValidationGroups.length > 0 ? 1.25 : 0 }}>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ display: 'block', mb: 0.5, fontSize: '0.65rem', lineHeight: 1.35 }}
                  >
                    Under-development and metadata-only validators are not scheduled by this checkbox list.
                  </Typography>
                  <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75 }}>
                    {metadataValidationAttachments.map((attachment) => {
                      const stateColor: 'success' | 'warning' = attachment.state === 'active'
                        ? 'success'
                        : 'warning'
                      return (
                        <Box
                          key={attachment.attachment_id}
                          sx={{
                            border: (theme) => `1px solid ${alpha(theme.palette.divider, 0.72)}`,
                            borderRadius: 1,
                            p: 0.85,
                            backgroundColor: (theme) => alpha(theme.palette.background.default, 0.22),
                          }}
                        >
                          <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 0.75 }}>
                            <Chip
                              size="small"
                              color={stateColor}
                              variant="outlined"
                              label={attachment.state === 'active' ? 'metadata' : validationStateLabel(attachment.state)}
                              sx={{ height: 18, fontSize: '0.6rem', mt: 0.1, flexShrink: 0 }}
                            />
                            <Box sx={{ flex: 1, minWidth: 0 }}>
                              <Typography
                                variant="body2"
                                sx={{
                                  fontSize: '0.72rem',
                                  fontWeight: 650,
                                  lineHeight: 1.3,
                                  overflowWrap: 'anywhere',
                                  wordBreak: 'break-word',
                                }}
                              >
                                {attachment.label}
                              </Typography>
                              <Typography
                                variant="caption"
                                color="text.secondary"
                                sx={{
                                  display: 'block',
                                  mt: 0.2,
                                  fontSize: '0.63rem',
                                  lineHeight: 1.3,
                                  overflowWrap: 'anywhere',
                                  wordBreak: 'break-word',
                                }}
                              >
                                {validationTargetText(attachment)}
                              </Typography>
                              <Typography
                                variant="caption"
                                color="text.secondary"
                                sx={{
                                  display: 'block',
                                  mt: 0.15,
                                  fontSize: '0.61rem',
                                  lineHeight: 1.3,
                                  overflowWrap: 'anywhere',
                                  wordBreak: 'break-word',
                                }}
                              >
                                {validationOwnerText(attachment)}
                              </Typography>
                              {attachment.state === 'under_development' && (
                                <Typography variant="caption" color="warning.main" sx={{ display: 'block', mt: 0.2, fontSize: '0.63rem' }}>
                                  {validationStateHelpText(attachment)}
                                </Typography>
                              )}
                            </Box>
                          </Box>
                        </Box>
                      )
                    })}
                  </Box>
                </Box>
              )}
            </Box>

            <Divider />
          </>
        )}

        {supportsOutputFormatting ? (
          <>
            <Divider />

            {outputBinding?.status === 'bound' ? (
              <Alert severity="info" icon={<SchemaIcon fontSize="inherit" />}>
                {outputBinding.sources.length === 1 ? (
                  <>
                    Configuring output for <strong>{outputBinding.sourceLabel}</strong> extraction.
                    This formatter receives only that extractor&apos;s result.
                  </>
                ) : (
                  <>
                    Configuring one output from <strong>{outputBinding.sources.length} source steps</strong>:{' '}
                    {outputBinding.sources.map((source) => source.sourceLabel).join(', ')}.
                    This formatter receives their results as one grouped input.
                  </>
                )}
              </Alert>
            ) : (
              <Alert severity="error" icon={<SchemaIcon fontSize="inherit" />}>
                {outputBinding?.status === 'duplicate'
                  ? 'The same source step is attached to this formatter more than once. Remove the duplicate connection.'
                  : outputBinding?.status === 'incompatible'
                    ? 'This formatter is connected to an incompatible step. Connect it only to extraction results or typed validation results.'
                    : 'This formatter is not configured. Connect at least one extraction result or typed validation result.'}
              </Alert>
            )}

            <Box>
              <FieldLabel>
                <Typography variant="caption" fontWeight={600}>
                  Output Options
                </Typography>
                <Tooltip title="When enabled, this formatter should carry supporting evidence from earlier steps into the final output whenever that evidence is available.">
                  <InfoOutlinedIcon sx={{ fontSize: 14, color: 'text.secondary' }} />
                </Tooltip>
              </FieldLabel>
              <FormControlLabel
                sx={{ alignItems: 'flex-start', m: 0 }}
                control={(
                  <Checkbox
                    size="small"
                    checked={includeEvidence}
                    onChange={(e) => setIncludeEvidence(e.target.checked)}
                  />
                )}
                label={(
                  <Box>
                    <Typography variant="body2" fontSize="0.75rem">
                      Include evidence in output
                    </Typography>
                    <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.65rem' }}>
                      Surface upstream evidence alongside the final chat or exported result.
                    </Typography>
                  </Box>
                )}
              />
            </Box>

            <Box>
              <FieldLabel>
                <Typography variant="caption" fontWeight={600}>
                  Output Filename Template
                </Typography>
                <Tooltip title="Controls the readable filename descriptor using built-in placeholders. Storage adds a trace ID prefix; include {{timestamp}} here when the filename should contain a timestamp.">
                  <InfoOutlinedIcon sx={{ fontSize: 14, color: 'text.secondary' }} />
                </Tooltip>
              </FieldLabel>
              <Box sx={{ mb: 1 }}>
                <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.65rem' }}>
                  Built-in variables:
                </Typography>
                <Box sx={{ mt: 0.5 }}>
                  {BUILT_IN_TEMPLATE_VARIABLES.map((variable) => (
                    <VariableChip
                      key={variable}
                      onClick={() => handleInsertOutputFilenameVariable(variable)}
                    >
                      {`{{${variable}}}`}
                    </VariableChip>
                  ))}
                </Box>
              </Box>
              <TextField
                fullWidth
                size="small"
                placeholder="{{input_filename_stem}}.tsv"
                value={outputFilenameTemplate}
                onChange={(e) => setOutputFilenameTemplate(e.target.value)}
                helperText="Applies before sanitization. Include {{timestamp}} in the template when a timestamp is wanted."
              />
            </Box>

            <Divider />
          </>
        ) : (
          <Divider />
        )}

        {/* Output Variable */}
        <Box>
          <FieldLabel>
            <Typography variant="caption" fontWeight={600}>
              Output Variable Name
            </Typography>
            <Tooltip title="Variable name to store this step's output for use in later steps">
              <InfoOutlinedIcon sx={{ fontSize: 14, color: 'text.secondary' }} />
            </Tooltip>
          </FieldLabel>
          <TextField
            fullWidth
            size="small"
            placeholder="e.g., validated_genes"
            value={outputKey}
            onChange={(e) => setOutputKey(e.target.value.replace(/[^a-zA-Z0-9_]/g, '_'))}
            sx={{
              '& input': {
                fontFamily: 'monospace',
                fontSize: '0.85rem',
              },
            }}
          />
        </Box>
      </EditorContent>

      <EditorFooter>
        <Button
          variant="contained"
          size="small"
          startIcon={<SaveIcon />}
          onClick={handleSave}
        >
          Apply
        </Button>
        <Button variant="outlined" size="small" onClick={onClose}>
          Cancel
        </Button>
        {onDelete && (
          <Button
            variant="outlined"
            size="small"
            color="error"
            startIcon={<DeleteIcon />}
            onClick={() => {
              onDelete(node!.id)
              onClose()
            }}
          >
            Delete
          </Button>
        )}
      </EditorFooter>
    </EditorContainer>
  )
}

export default NodeEditor
