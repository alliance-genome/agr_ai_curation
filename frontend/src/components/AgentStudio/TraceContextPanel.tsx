/**
 * TraceContextPanel Component
 *
 * Displays trace context when opening Prompt Explorer from the triple-dot menu.
 * Shows:
 * - User query and response preview
 * - Agents that were executed (clickable to navigate)
 * - Routing decisions
 * - Tool calls summary
 * - Performance metrics
 */

import {
  Box,
  Typography,
  Paper,
  Chip,
  Collapse,
  IconButton,
  Tooltip,
} from '@mui/material'
import { styled, alpha } from '@mui/material/styles'
import { useState } from 'react'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import ExpandLessIcon from '@mui/icons-material/ExpandLess'
import TimelineIcon from '@mui/icons-material/Timeline'
import AccountTreeIcon from '@mui/icons-material/AccountTree'
import BuildIcon from '@mui/icons-material/Build'
import TokenIcon from '@mui/icons-material/DataObject'
import TimerIcon from '@mui/icons-material/Timer'

import type { TraceContext } from '@/types/promptExplorer'

const PanelContainer = styled(Paper)(({ theme }) => ({
  backgroundColor: alpha(theme.palette.info.main, 0.05),
  border: `1px solid ${alpha(theme.palette.info.main, 0.2)}`,
  borderRadius: theme.shape.borderRadius,
  overflow: 'hidden',
}))

const PanelHeader = styled(Box)(({ theme }) => ({
  padding: theme.spacing(1.5, 2),
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  cursor: 'pointer',
  '&:hover': {
    backgroundColor: alpha(theme.palette.info.main, 0.08),
  },
}))

const PanelContent = styled(Box)(({ theme }) => ({
  padding: theme.spacing(2),
  paddingTop: 0,
}))

const MetricChip = styled(Chip)(() => ({
  height: 24,
  '& .MuiChip-icon': {
    fontSize: 14,
  },
  '& .MuiChip-label': {
    fontSize: '0.75rem',
  },
}))

const AgentChip = styled(Chip, {
  shouldForwardProp: (prop) => prop !== 'isSelected',
})<{ isSelected?: boolean }>(({ theme, isSelected }) => ({
  cursor: 'pointer',
  transition: 'all 0.2s ease',
  backgroundColor: isSelected
    ? theme.palette.primary.main
    : alpha(theme.palette.primary.main, 0.1),
  color: isSelected ? theme.palette.primary.contrastText : theme.palette.primary.main,
  '&:hover': {
    backgroundColor: isSelected
      ? theme.palette.primary.dark
      : alpha(theme.palette.primary.main, 0.2),
  },
}))

interface TraceContextPanelProps {
  context: TraceContext
  onAgentClick: (agentId: string, modId?: string) => void
  selectedAgentId: string | null
}

function TraceContextPanel({
  context,
  onAgentClick,
  selectedAgentId,
}: TraceContextPanelProps) {
  const [expanded, setExpanded] = useState(true)

  // Format duration
  const formatDuration = (ms?: number) => {
    if (!ms) return 'N/A'
    if (ms < 1000) return `${ms}ms`
    return `${(ms / 1000).toFixed(1)}s`
  }

  // Format tokens
  const formatTokens = (tokens?: number) => {
    if (!tokens) return 'N/A'
    if (tokens < 1000) return tokens.toString()
    return `${(tokens / 1000).toFixed(1)}k`
  }

  return (
    <PanelContainer elevation={0}>
      <PanelHeader onClick={() => setExpanded(!expanded)}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <TimelineIcon color="info" fontSize="small" />
          <Typography variant="subtitle2" fontWeight={600}>
            Trace Context
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {context.trace_id.slice(0, 8)}...
          </Typography>
        </Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          {/* Quick metrics */}
          <Tooltip title="Total duration">
            <MetricChip
              icon={<TimerIcon />}
              label={formatDuration(context.total_duration_ms)}
              size="small"
              variant="outlined"
            />
          </Tooltip>
          <Tooltip title="Total tokens">
            <MetricChip
              icon={<TokenIcon />}
              label={formatTokens(context.total_tokens)}
              size="small"
              variant="outlined"
            />
          </Tooltip>
          <IconButton size="small">
            {expanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
          </IconButton>
        </Box>
      </PanelHeader>

      <Collapse in={expanded}>
        <PanelContent>
          {/* User Query */}
          <Box sx={{ mb: 2 }}>
            <Typography variant="caption" color="text.secondary" fontWeight={600}>
              Query
            </Typography>
            <Typography variant="body2" sx={{ mt: 0.5 }}>
              {context.user_query}
            </Typography>
          </Box>

          {/* Agents Executed */}
          <Box sx={{ mb: 2 }}>
            <Typography variant="caption" color="text.secondary" fontWeight={600}>
              Agents Executed ({context.prompts_executed.length})
            </Typography>
            <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', mt: 0.5 }}>
              {context.prompts_executed.map((exec, idx) => (
                <Tooltip
                  key={`${exec.agent_id}-${idx}`}
                  title={
                    <Box>
                      <Typography variant="caption" display="block">
                        {exec.agent_name}
                      </Typography>
                      {exec.mod_applied && (
                        <Typography variant="caption" display="block">
                          MOD: {exec.mod_applied}
                        </Typography>
                      )}
                      {exec.tokens_used && (
                        <Typography variant="caption" display="block">
                          Tokens: {exec.tokens_used}
                        </Typography>
                      )}
                    </Box>
                  }
                >
                  <AgentChip
                    label={exec.agent_name}
                    size="small"
                    isSelected={selectedAgentId === exec.agent_id}
                    onClick={() => onAgentClick(exec.agent_id, exec.mod_applied)}
                  />
                </Tooltip>
              ))}
            </Box>
          </Box>

          {/* Routing Decisions */}
          {context.routing_decisions.length > 0 && (
            <Box sx={{ mb: 2 }}>
              <Typography variant="caption" color="text.secondary" fontWeight={600}>
                Routing Path
              </Typography>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mt: 0.5, flexWrap: 'wrap' }}>
                {context.routing_decisions.map((decision, idx) => (
                  <Box key={idx} sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                    <Chip label={decision.from_agent} size="small" variant="outlined" />
                    <AccountTreeIcon fontSize="small" color="action" />
                    <Chip label={decision.to_agent} size="small" variant="outlined" />
                    {idx < context.routing_decisions.length - 1 && (
                      <Typography color="text.secondary" sx={{ mx: 0.5 }}>
                        →
                      </Typography>
                    )}
                  </Box>
                ))}
              </Box>
            </Box>
          )}

          {/* Tool Calls Summary */}
          {context.tool_calls.length > 0 && (
            <Box>
              <Typography variant="caption" color="text.secondary" fontWeight={600}>
                Tool Calls ({context.tool_calls.length})
              </Typography>
              <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', mt: 0.5 }}>
                {/* Group by tool name and show count */}
                {Object.entries(
                  context.tool_calls.reduce((acc, call) => {
                    acc[call.name] = (acc[call.name] || 0) + 1
                    return acc
                  }, {} as Record<string, number>)
                ).map(([name, count]) => (
                  <Chip
                    key={name}
                    icon={<BuildIcon />}
                    label={`${name}${count > 1 ? ` ×${count}` : ''}`}
                    size="small"
                    variant="outlined"
                  />
                ))}
              </Box>
            </Box>
          )}
        </PanelContent>
      </Collapse>
    </PanelContainer>
  )
}

export default TraceContextPanel
