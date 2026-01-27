/**
 * DeletableEdge Component
 *
 * Custom edge with an X button that appears on hover.
 * Allows users to delete edge connections by clicking the X.
 */

import { useState, useCallback } from 'react'
import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  useReactFlow,
  type EdgeProps,
} from 'reactflow'
import { styled, alpha } from '@mui/material/styles'
import CloseIcon from '@mui/icons-material/Close'

// Styled delete button that appears on hover
const DeleteButton = styled('button')<{ visible: boolean }>(({ theme, visible }) => ({
  width: 20,
  height: 20,
  borderRadius: '50%',
  border: `1px solid ${theme.palette.divider}`,
  backgroundColor: theme.palette.background.paper,
  color: theme.palette.text.secondary,
  cursor: 'pointer',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  padding: 0,
  opacity: visible ? 1 : 0,
  transition: 'opacity 0.15s ease, background-color 0.15s ease, color 0.15s ease',
  pointerEvents: visible ? 'auto' : 'none',
  boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
  '&:hover': {
    backgroundColor: theme.palette.error.main,
    borderColor: theme.palette.error.main,
    color: theme.palette.error.contrastText,
  },
  '& svg': {
    fontSize: 14,
  },
}))

// Invisible wider path for easier hover detection
const HoverPath = styled('path')({
  fill: 'none',
  strokeWidth: 20,
  stroke: 'transparent',
  cursor: 'pointer',
})

function DeletableEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style = {},
  markerEnd,
}: EdgeProps) {
  const { setEdges } = useReactFlow()
  const [isHovered, setIsHovered] = useState(false)

  // Generate the bezier path
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  })

  // Handle edge deletion
  const onDelete = useCallback(
    (event: React.MouseEvent) => {
      event.stopPropagation()
      setEdges((edges) => edges.filter((edge) => edge.id !== id))
    },
    [id, setEdges]
  )

  // Handle hover states
  const handleMouseEnter = useCallback(() => setIsHovered(true), [])
  const handleMouseLeave = useCallback(() => setIsHovered(false), [])

  return (
    <>
      {/* Invisible wider path for easier hover detection */}
      <HoverPath
        d={edgePath}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
      />

      {/* Visible edge path */}
      <BaseEdge
        path={edgePath}
        markerEnd={markerEnd}
        style={{
          ...style,
          stroke: isHovered ? '#1976d2' : style.stroke,
          strokeWidth: isHovered ? 2.5 : (style.strokeWidth as number) || 2,
        }}
      />

      {/* Delete button positioned at edge center */}
      <EdgeLabelRenderer>
        <div
          style={{
            position: 'absolute',
            transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
            pointerEvents: 'all',
          }}
          className="nodrag nopan"
          onMouseEnter={handleMouseEnter}
          onMouseLeave={handleMouseLeave}
        >
          <DeleteButton
            visible={isHovered}
            onClick={onDelete}
            title="Delete connection"
          >
            <CloseIcon />
          </DeleteButton>
        </div>
      </EdgeLabelRenderer>
    </>
  )
}

export default DeletableEdge
