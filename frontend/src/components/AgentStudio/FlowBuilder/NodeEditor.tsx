/**
 * NodeEditor Component
 *
 * Configuration panel for editing a selected flow node.
 * Allows setting step goal, custom instructions, input source, and output variable.
 */

import { useState, useEffect } from 'react'
import {
  Box,
  Typography,
  TextField,
  Paper,
  IconButton,
  Checkbox,
  Radio,
  RadioGroup,
  FormControlLabel,
  FormControl,
  FormLabel,
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

import type { NodeEditorProps, InputSource, ValidationAttachmentSelection } from './types'
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

function NodeEditor({ node, onSave, onClose, onDelete, availableVariables, onViewPrompts, onViewDomainEnvelope, hasIncomingEdge = false, onMarkManuallyConfigured }: NodeEditorProps) {
  const { agents: agentMetadata } = useAgentMetadata()

  // Form state
  const [customInstructions, setCustomInstructions] = useState('')
  const [inputSource, setInputSource] = useState<InputSource>('previous_output')
  const [customInput, setCustomInput] = useState('')
  const [includeEvidence, setIncludeEvidence] = useState(false)
  const [outputFilenameTemplate, setOutputFilenameTemplate] = useState('')
  const [outputKey, setOutputKey] = useState('')
  const [validationAttachments, setValidationAttachments] = useState<ValidationAttachmentSelection[]>([])

  // Check if this is a PDF agent (input source is hardcoded to PDF document)
  const isPdfAgent = node?.data.agent_id === 'pdf_extraction'
  const agentMetadataEntry = node ? agentMetadata[node.data.agent_id] : undefined
  const domainEnvelopeMetadata = agentMetadataEntry?.domain_envelope
  const isValidationAgentNode = node
    ? isValidationAgentFromMetadata(node.data.agent_id, agentMetadata)
    : false
  const supportsOutputFormatting = node
    ? isOutputFormatterAgentFromMetadata(node.data.agent_id, agentMetadata)
    : false
  const customInputVariables = Array.from(
    new Set([...availableVariables, ...BUILT_IN_TEMPLATE_VARIABLES])
  )
  const customInputError = inputSource === 'custom' && !customInput.trim()
  const customInstructionsLabel = isValidationAgentNode
    ? 'Validation Steering Prompt (Optional)'
    : 'Custom Instructions (Optional)'
  const customInstructionsTooltip = isValidationAgentNode
    ? 'Use this prompt to focus a validation agent on a specific envelope object, field path, or curator concern. It is saved with this flow step.'
    : 'These instructions take the highest priority and override the agent\'s base prompt and group rules for this flow step. Use them to add constraints or focus the agent\'s behavior.'
  const missingOptOutReason = validationAttachments.some(
    (attachment) => (
      attachment.state === 'active'
      && !attachment.enabled
      && attachment.allow_opt_out
      && attachment.opt_out_reason_required
      && !attachment.opt_out_reason?.trim()
    )
  )
  const actionableValidationAttachments = validationAttachments.filter(
    (attachment) => attachment.state === 'active' && Boolean(attachment.validator_binding_id)
  )
  const metadataValidationAttachments = validationAttachments.filter(
    (attachment) => !(attachment.state === 'active' && Boolean(attachment.validator_binding_id))
  )

  // Initialize form when node changes
  useEffect(() => {
    if (node) {
      setCustomInstructions(node.data.custom_instructions || '')
      // PDF agent always uses 'previous_output' (representing PDF document)
      // Other agents default to 'previous_output' if they have incoming edge, else 'custom'
      if (isPdfAgent) {
        setInputSource('previous_output')
      } else {
        setInputSource(node.data.input_source || (hasIncomingEdge ? 'previous_output' : 'custom'))
      }
      setCustomInput(node.data.custom_input || '')
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
  }, [node, isPdfAgent, hasIncomingEdge, agentMetadata])

  // Handle save
  const handleSave = () => {
    if (!node) return
    if (customInputError) return
    if (missingOptOutReason) return

    const nextIncludeEvidence = agentMetadataEntry
      ? resolveOutputFormatterIncludeEvidence(
          node.data.agent_id,
          agentMetadata,
          includeEvidence,
        )
      : node.data.include_evidence

    onSave(node.id, {
      custom_instructions: customInstructions || undefined,
      input_source: inputSource,
      custom_input: inputSource === 'custom' ? customInput.trim() : undefined,
      include_evidence: nextIncludeEvidence,
      output_filename_template: supportsOutputFormatting
        ? outputFilenameTemplate.trim() || undefined
        : undefined,
      output_key: outputKey,
      validation_attachments: validationAttachments.length > 0
        ? validationAttachments
        : undefined,
    })

    // Mark as manually configured when user saves (user has taken control)
    onMarkManuallyConfigured?.(node.id)

    onClose()
  }

  // Insert variable into custom input
  const handleInsertVariable = (variable: string) => {
    setCustomInput((prev) => prev + `{{${variable}}}`)
  }

  const handleInsertOutputFilenameVariable = (variable: string) => {
    setOutputFilenameTemplate((prev) => prev + `{{${variable}}}`)
  }

  const handleValidationToggle = (attachmentId: string, enabled: boolean) => {
    setValidationAttachments((current) => current.map((attachment) => (
      attachment.attachment_id === attachmentId
        ? { ...attachment, enabled }
        : attachment
    )))
  }

  const handleValidationReasonChange = (attachmentId: string, optOutReason: string) => {
    setValidationAttachments((current) => current.map((attachment) => (
      attachment.attachment_id === attachmentId
        ? { ...attachment, opt_out_reason: optOutReason }
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
            Custom validation agents persist as regular flow steps. Use the steering prompt to target the envelope object, field path, or validation question.
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

        {validationAttachments.length > 0 && (
          <>
            <Box>
              <FieldLabel>
                <Typography variant="caption" fontWeight={600}>
                  Validation Attachments
                </Typography>
                <Tooltip title="Checked active validators run automatically. Planned, blocked, and metadata-only validators are shown below as read-only domain-pack context.">
                  <InfoOutlinedIcon sx={{ fontSize: 14, color: 'text.secondary' }} />
                </Tooltip>
              </FieldLabel>
              {actionableValidationAttachments.length > 0 && (
                <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
                  {actionableValidationAttachments.map((attachment) => {
                  const hasBinding = Boolean(attachment.validator_binding_id)
                  const canToggle = attachment.state === 'active'
                    && hasBinding
                    && attachment.allow_opt_out
                  const showReason = attachment.state === 'active'
                    && !attachment.enabled
                    && attachment.allow_opt_out
                    && attachment.opt_out_reason_required
                  const reasonError = showReason
                    && !attachment.opt_out_reason?.trim()
                  const stateColor: 'success' | 'warning' | 'error' = attachment.state === 'active'
                    ? 'success'
                    : attachment.state === 'planned'
                      ? 'warning'
                      : 'error'
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
                              {attachment.label || attachment.validator_id}
                            </Typography>
                            <Chip
                              size="small"
                              color={stateColor}
                              label={attachment.state}
                              sx={{ height: 18, fontSize: '0.6rem' }}
                            />
                            {attachment.export_blocking && (
                              <Chip
                                size="small"
                                color="error"
                                variant="outlined"
                                label="blocks export"
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
                            {[attachment.object_type, attachment.field_path].filter(Boolean).join(' / ') || attachment.scope}
                          </Typography>
                          {attachment.state === 'blocked' && attachment.blocked_by && (
                            <Typography variant="caption" color="error.main" sx={{ display: 'block', fontSize: '0.65rem' }}>
                              Blocked by {attachment.blocked_by}
                            </Typography>
                          )}
                          {attachment.state === 'planned' && (
                            <Typography variant="caption" color="warning.main" sx={{ display: 'block', fontSize: '0.65rem' }}>
                              Planned metadata only
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
                      {showReason && (
                        <TextField
                          fullWidth
                          size="small"
                          value={attachment.opt_out_reason || ''}
                          onChange={(event) => handleValidationReasonChange(
                            attachment.attachment_id,
                            event.target.value
                          )}
                          placeholder="Reason for disabling this validator"
                          error={reasonError}
                          helperText={reasonError ? 'A reason is required for this opt-out.' : undefined}
                          sx={{ mt: 1 }}
                        />
                      )}
                    </Box>
                  )
                })}
                </Box>
              )}

              {metadataValidationAttachments.length > 0 && (
                <Box sx={{ mt: actionableValidationAttachments.length > 0 ? 1.25 : 0 }}>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ display: 'block', mb: 0.5, fontSize: '0.65rem', lineHeight: 1.35 }}
                  >
                    Planned, blocked, and metadata-only validators are not scheduled by this checkbox list.
                  </Typography>
                  <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75 }}>
                    {metadataValidationAttachments.map((attachment) => {
                      const stateColor: 'success' | 'warning' | 'error' = attachment.state === 'active'
                        ? 'success'
                        : attachment.state === 'planned'
                          ? 'warning'
                          : 'error'
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
                              label={attachment.state === 'active' ? 'metadata' : attachment.state}
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
                                {attachment.label || attachment.validator_id}
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
                                {[attachment.object_type, attachment.field_path].filter(Boolean).join(' / ') || attachment.scope}
                              </Typography>
                              {attachment.state === 'blocked' && attachment.blocked_by && (
                                <Typography variant="caption" color="error.main" sx={{ display: 'block', mt: 0.2, fontSize: '0.63rem' }}>
                                  Blocked by {attachment.blocked_by}
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

        {/* Input Source */}
        {isPdfAgent ? (
          /* PDF Agent - Fixed input source (PDF document) */
          <Box>
            <Typography variant="caption" fontWeight={600} sx={{ display: 'block', mb: 0.5 }}>
              Input Source
            </Typography>
            <Box
              sx={{
                py: 1,
                px: 1.5,
                borderRadius: 1,
                backgroundColor: (theme) => alpha(theme.palette.info.main, 0.08),
                border: (theme) => `1px solid ${alpha(theme.palette.info.main, 0.3)}`,
              }}
            >
              <Typography variant="body2" fontSize="0.75rem" color="text.secondary">
                📄 PDF Document (automatic)
              </Typography>
              <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.65rem' }}>
                This agent always receives the uploaded PDF as input
              </Typography>
            </Box>
          </Box>
        ) : (
          /* Other Agents - Choice of Previous Step Output or Custom */
          <FormControl component="fieldset">
            <FormLabel component="legend" sx={{ fontSize: '0.75rem', fontWeight: 600 }}>
              Input Source
            </FormLabel>
            <RadioGroup
              value={inputSource}
              onChange={(e) => setInputSource(e.target.value as InputSource)}
            >
              <Tooltip
                title={!hasIncomingEdge ? 'Connect this node to a previous step to enable' : ''}
                placement="right"
              >
                <FormControlLabel
                  value="previous_output"
                  control={<Radio size="small" />}
                  disabled={!hasIncomingEdge}
                  label={
                    <Typography
                      variant="body2"
                      fontSize="0.75rem"
                      color={!hasIncomingEdge ? 'text.disabled' : 'text.primary'}
                    >
                      Previous Step Output
                    </Typography>
                  }
                />
              </Tooltip>
              <FormControlLabel
                value="custom"
                control={<Radio size="small" />}
                label={
                  <Typography variant="body2" fontSize="0.75rem">
                    Custom (with variables)
                  </Typography>
                }
              />
            </RadioGroup>
          </FormControl>
        )}

        {/* Custom Input Template */}
        {inputSource === 'custom' && (
          <Box>
            <FieldLabel>
              <Typography variant="caption" fontWeight={600}>
                Custom Input Template
              </Typography>
            </FieldLabel>
            {customInputVariables.length > 0 && (
              <Box sx={{ mb: 1 }}>
                <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.65rem' }}>
                  Click to insert:
                </Typography>
                <Box sx={{ mt: 0.5 }}>
                  {customInputVariables.map((v) => (
                    <VariableChip key={v} onClick={() => handleInsertVariable(v)}>
                      {`{{${v}}}`}
                    </VariableChip>
                  ))}
                </Box>
              </Box>
            )}
            <TextField
              fullWidth
              size="small"
              placeholder="e.g., Validate these genes: {{pdf_output}}"
              value={customInput}
              onChange={(e) => setCustomInput(e.target.value)}
              multiline
              rows={2}
              error={customInputError}
              helperText={customInputError ? 'A custom input template is required for this setting.' : undefined}
            />
          </Box>
        )}

        {supportsOutputFormatting ? (
          <>
            <Divider />

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
                <Tooltip title="Controls the readable filename descriptor for formatter outputs using {{variable}} placeholders. Stored files still keep the trace ID prefix and timestamp suffix.">
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
                helperText="Applies before sanitization. Example final file: traceid_input_filename_stem_20260410T120000Z.tsv"
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
          disabled={customInputError || missingOptOutReason}
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
