/**
 * FlowNode Component
 *
 * Custom React Flow node for agents in the flow canvas.
 * Displays agent icon, name, and step preview.
 * Click to expand and edit in NodeEditor.
 */

import { memo } from 'react'
import { Handle, Position } from 'reactflow'
import { Box, Typography, Paper, Tooltip } from '@mui/material'
import { styled, alpha } from '@mui/material/styles'
import ErrorOutlineIcon from '@mui/icons-material/ErrorOutline'

import type { AgentNodeData } from './types'
import { useAgentIcon } from '@/hooks/useAgentIcon'

const NodeContainer = styled(Paper, {
  shouldForwardProp: (prop) => prop !== 'isSelected' && prop !== 'hasError' && prop !== 'isTaskInput',
})<{ isSelected?: boolean; hasError?: boolean; isTaskInput?: boolean }>(({ theme, isSelected, hasError, isTaskInput }) => ({
  minWidth: 140,
  maxWidth: 180,
  padding: theme.spacing(1),
  borderRadius: theme.shape.borderRadius,
  backgroundColor: isTaskInput
    ? alpha(theme.palette.warning.light, 0.15)
    : alpha(theme.palette.background.paper, 0.95),
  border: `2px solid ${
    hasError
      ? theme.palette.error.main
      : isSelected
        ? isTaskInput
          ? theme.palette.warning.main
          : theme.palette.primary.main
        : isTaskInput
          ? alpha(theme.palette.warning.main, 0.5)
          : theme.palette.divider
  }`,
  cursor: 'pointer',
  transition: 'all 0.2s ease',
  '&:hover': {
    borderColor: hasError
      ? theme.palette.error.main
      : isTaskInput
        ? theme.palette.warning.main
        : theme.palette.primary.light,
    boxShadow: `0 4px 12px ${alpha(theme.palette.common.black, 0.15)}`,
  },
}))

const NodeHeader = styled(Box)(({ theme }) => ({
  display: 'flex',
  alignItems: 'center',
  gap: theme.spacing(0.75),
  marginBottom: theme.spacing(0.5),
}))

const IconWrapper = styled(Box, {
  shouldForwardProp: (prop) => prop !== 'isTaskInput',
})<{ isTaskInput?: boolean }>(({ theme, isTaskInput }) => ({
  fontSize: '1.2rem',
  lineHeight: 1,
  width: 24,
  height: 24,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  borderRadius: theme.shape.borderRadius,
  backgroundColor: isTaskInput
    ? alpha(theme.palette.warning.main, 0.2)
    : alpha(theme.palette.primary.main, 0.1),
}))

const AgentName = styled(Typography)(() => ({
  fontWeight: 600,
  fontSize: '0.75rem',
  lineHeight: 1.2,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
  flex: 1,
}))

const StepPreview = styled(Typography)(({ theme }) => ({
  fontSize: '0.65rem',
  color: theme.palette.text.secondary,
  lineHeight: 1.3,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  display: '-webkit-box',
  WebkitLineClamp: 2,
  WebkitBoxOrient: 'vertical',
}))

const HandleStyled = styled(Handle)(({ theme }) => ({
  width: 8,
  height: 8,
  backgroundColor: theme.palette.primary.main,
  border: `2px solid ${theme.palette.background.paper}`,
  '&:hover': {
    backgroundColor: theme.palette.primary.light,
    transform: 'scale(1.2)',
  },
}))

const ErrorBadge = styled(Box)(({ theme }) => ({
  position: 'absolute',
  top: -6,
  right: -6,
  color: theme.palette.error.main,
  backgroundColor: theme.palette.background.paper,
  borderRadius: '50%',
}))

interface FlowNodeComponentProps {
  data: AgentNodeData
  selected: boolean
}

function FlowNodeComponent({ data, selected }: FlowNodeComponentProps) {
  // Get icon from registry via hook
  const icon = useAgentIcon(data.agent_id)
  const hasError = data.hasError
  const isTaskInput = data.agent_id === 'task_input'

  // For task_input nodes, show task_instructions; for agents, show custom_instructions
  const previewText = isTaskInput
    ? data.task_instructions || ''
    : data.custom_instructions || ''

  const emptyMessage = isTaskInput
    ? 'Click to add instructions'
    : 'Click to configure'

  const tooltipTitle = previewText || (isTaskInput ? 'No task instructions' : 'No custom instructions')

  return (
    <>
      {/* Input handle (top) - NOT shown for task_input nodes */}
      {!isTaskInput && <HandleStyled type="target" position={Position.Top} />}

      <NodeContainer
        isSelected={selected}
        hasError={hasError}
        isTaskInput={isTaskInput}
        elevation={selected ? 4 : 1}
      >
        {hasError && (
          <Tooltip title={data.errorMessage || 'Configuration error'}>
            <ErrorBadge>
              <ErrorOutlineIcon fontSize="small" />
            </ErrorBadge>
          </Tooltip>
        )}

        <NodeHeader>
          <IconWrapper isTaskInput={isTaskInput}>{icon}</IconWrapper>
          <AgentName>{data.agent_display_name}</AgentName>
        </NodeHeader>

        <Tooltip title={tooltipTitle} placement="bottom" enterDelay={500}>
          <StepPreview>
            {previewText ? previewText : <em>{emptyMessage}</em>}
          </StepPreview>
        </Tooltip>
      </NodeContainer>

      {/* Output handle (bottom) */}
      <HandleStyled type="source" position={Position.Bottom} />
    </>
  )
}

// Memoize to prevent unnecessary re-renders during drag/pan
export default memo(FlowNodeComponent)
