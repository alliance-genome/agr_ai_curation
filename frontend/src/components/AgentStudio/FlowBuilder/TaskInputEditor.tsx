/**
 * TaskInputEditor Component
 *
 * Configuration panel for editing task_input nodes.
 * These nodes define the curator's initial instructions that start the flow.
 */

import { useState, useEffect } from 'react'
import {
  Box,
  Typography,
  TextField,
  Paper,
  IconButton,
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

import type { AgentNode, AgentNodeData } from './types'
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
  backgroundColor: alpha(theme.palette.warning.main, 0.08),
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
  backgroundColor: alpha(theme.palette.warning.main, 0.15),
}))

const FieldLabel = styled(Box)(({ theme }) => ({
  display: 'flex',
  alignItems: 'center',
  gap: theme.spacing(0.5),
  marginBottom: theme.spacing(0.5),
}))

export interface TaskInputEditorProps {
  /** The task_input node being edited */
  node: AgentNode | null
  /** Callback to save changes */
  onSave: (nodeId: string, data: Partial<AgentNodeData>) => void
  /** Callback to close the editor */
  onClose: () => void
  /** Callback to delete the node */
  onDelete?: (nodeId: string) => void
}

function TaskInputEditor({ node, onSave, onClose, onDelete }: TaskInputEditorProps) {
  // Form state
  const [taskInstructions, setTaskInstructions] = useState('')
  const [outputKey, setOutputKey] = useState('')
  const [error, setError] = useState<string | null>(null)

  // Initialize form when node changes
  useEffect(() => {
    if (node) {
      setTaskInstructions(node.data.task_instructions || '')
      setOutputKey(node.data.output_key || 'task_input')
      setError(null)
    }
  }, [node])

  // Handle save with validation
  const handleSave = () => {
    if (!node) return

    // Validate task_instructions is not empty
    if (!taskInstructions.trim()) {
      setError('Task instructions are required')
      return
    }

    // Validate output_key matches backend pattern: ^[a-zA-Z_][a-zA-Z0-9_]*$
    const trimmedKey = outputKey.trim()
    if (!trimmedKey) {
      setError('Output variable name is required')
      return
    }
    if (!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(trimmedKey)) {
      setError('Output variable must start with a letter or underscore and contain only letters, numbers, and underscores')
      return
    }

    onSave(node.id, {
      task_instructions: taskInstructions.trim(),
      output_key: trimmedKey,
    })
    onClose()
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
        {/* Info Alert */}
        <Alert severity="info" sx={{ py: 0.5, fontSize: '0.75rem' }}>
          This node defines the task that will be passed to the flow. Describe what you want the
          agents to accomplish.
        </Alert>

        {/* Task Instructions (Required) */}
        <Box>
          <FieldLabel>
            <Typography variant="caption" fontWeight={600}>
              Task Instructions *
            </Typography>
            <Tooltip title="Describe the curation task. This will be passed to the first agent in the flow.">
              <InfoOutlinedIcon sx={{ fontSize: 14, color: 'text.secondary' }} />
            </Tooltip>
          </FieldLabel>
          <TextField
            fullWidth
            size="small"
            placeholder="e.g., Extract all gene names mentioned in this paper and validate them against the Alliance database. Return only validated genes with their IDs."
            value={taskInstructions}
            onChange={(e) => {
              setTaskInstructions(e.target.value)
              if (error) setError(null)
            }}
            multiline
            rows={6}
            error={!!error}
            helperText={error}
            sx={{
              '& .MuiInputBase-root': {
                fontSize: '0.85rem',
              },
            }}
          />
          <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: 'block' }}>
            Be specific about what data to extract and how to format the output.
          </Typography>
        </Box>

        <Divider />

        {/* Output Variable */}
        <Box>
          <FieldLabel>
            <Typography variant="caption" fontWeight={600}>
              Output Variable Name
            </Typography>
            <Tooltip title="Variable name to reference this task's instructions in downstream steps">
              <InfoOutlinedIcon sx={{ fontSize: 14, color: 'text.secondary' }} />
            </Tooltip>
          </FieldLabel>
          <TextField
            fullWidth
            size="small"
            placeholder="e.g., task_input"
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
        <Button variant="contained" size="small" startIcon={<SaveIcon />} onClick={handleSave}>
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

export default TaskInputEditor
