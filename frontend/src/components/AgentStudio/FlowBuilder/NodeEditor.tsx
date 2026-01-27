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
  Radio,
  RadioGroup,
  FormControlLabel,
  FormControl,
  FormLabel,
  Button,
  Divider,
  Tooltip,
  Alert,
} from '@mui/material'
import { styled, alpha } from '@mui/material/styles'
import CloseIcon from '@mui/icons-material/Close'
import SaveIcon from '@mui/icons-material/Save'
import DeleteIcon from '@mui/icons-material/Delete'
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined'
import DescriptionIcon from '@mui/icons-material/Description'

import type { NodeEditorProps, InputSource } from './types'
import { useAgentIcon } from '@/hooks/useAgentIcon'

const EditorContainer = styled(Paper)(({ theme }) => ({
  position: 'absolute',
  top: 0,
  right: 0,
  bottom: 0,
  width: '100%',
  maxWidth: 320,
  backgroundColor: theme.palette.background.paper,
  borderLeft: `1px solid ${theme.palette.divider}`,
  display: 'flex',
  flexDirection: 'column',
  zIndex: 10,
  overflow: 'hidden',
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

function NodeEditor({ node, onSave, onClose, onDelete, availableVariables, onViewPrompts, hasIncomingEdge = false, onMarkManuallyConfigured }: NodeEditorProps) {
  // Form state
  const [customInstructions, setCustomInstructions] = useState('')
  const [inputSource, setInputSource] = useState<InputSource>('previous_output')
  const [customInput, setCustomInput] = useState('')
  const [outputKey, setOutputKey] = useState('')

  // Check if this is a PDF agent (input source is hardcoded to PDF document)
  const isPdfAgent = node?.data.agent_id === 'pdf'

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
      setOutputKey(node.data.output_key || `${node.data.agent_id}_output`)
    }
  }, [node, isPdfAgent, hasIncomingEdge])

  // Handle save
  const handleSave = () => {
    if (!node) return

    onSave(node.id, {
      custom_instructions: customInstructions || undefined,
      input_source: inputSource,
      custom_input: inputSource === 'custom' ? customInput : undefined,
      output_key: outputKey,
    })

    // Mark as manually configured when user saves (user has taken control)
    onMarkManuallyConfigured?.(node.id)

    onClose()
  }

  // Insert variable into custom input
  const handleInsertVariable = (variable: string) => {
    setCustomInput((prev) => prev + `{{${variable}}}`)
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
              View base prompt & MOD rules
            </Typography>
          </Box>
        )}

        {/* Custom Instructions */}
        <Box>
          <FieldLabel>
            <Typography variant="caption" fontWeight={600}>
              Custom Instructions (Optional)
            </Typography>
            <Tooltip title="Additional context or constraints for this step">
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
            {availableVariables.length > 0 && (
              <Box sx={{ mb: 1 }}>
                <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.65rem' }}>
                  Click to insert:
                </Typography>
                <Box sx={{ mt: 0.5 }}>
                  {availableVariables.map((v) => (
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
            />
          </Box>
        )}

        <Divider />

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
