/**
 * AgentPalette Component
 *
 * Draggable list of available agents for adding to the flow canvas.
 * Fetches from /api/agent-studio/catalog and filters out Routing category.
 * Agents are grouped by subcategory with collapsible sections.
 */

import { useState, useEffect, useMemo } from 'react'
import {
  Box,
  Typography,
  List,
  ListItem,
  Paper,
  Collapse,
  IconButton,
  CircularProgress,
  Alert,
  Tooltip,
  TextField,
  InputAdornment,
} from '@mui/material'
import { styled, alpha } from '@mui/material/styles'
import ExpandLessIcon from '@mui/icons-material/ExpandLess'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import DragIndicatorIcon from '@mui/icons-material/DragIndicator'
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown'
import KeyboardArrowRightIcon from '@mui/icons-material/KeyboardArrowRight'
import SearchIcon from '@mui/icons-material/Search'
import ClearIcon from '@mui/icons-material/Clear'

import type { AgentPaletteProps, AgentInfo } from './types'
import { useAgentMetadata } from '@/contexts/AgentMetadataContext'
import { fetchPromptCatalog } from '@/services/agentStudioService'
import logger from '@/services/logger'

// Define the display order for subcategories
const SUBCATEGORY_ORDER = ['Input', 'PDF Extraction', 'Data Validation', 'Output', 'My Custom Agents']

const PaletteContainer = styled(Paper)(({ theme }) => ({
  backgroundColor: alpha(theme.palette.background.paper, 0.95),
  borderRadius: theme.shape.borderRadius,
  overflow: 'hidden',
  display: 'flex',
  flexDirection: 'column',
  flex: 1,
  minHeight: 0,
}))

const PaletteHeader = styled(Box)(({ theme }) => ({
  padding: theme.spacing(1, 1.5),
  borderBottom: `1px solid ${theme.palette.divider}`,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  backgroundColor: alpha(theme.palette.primary.main, 0.05),
}))

const PaletteContent = styled(Box)(({ theme }) => ({
  flex: 1,
  overflow: 'auto',
  minHeight: 0,
  padding: theme.spacing(0.5),
}))

const SearchBox = styled(Box)(({ theme }) => ({
  padding: theme.spacing(0.75),
  borderBottom: `1px solid ${theme.palette.divider}`,
}))

const SubcategoryHeader = styled(Box)(({ theme }) => ({
  display: 'flex',
  alignItems: 'center',
  padding: theme.spacing(0.5, 0.75),
  cursor: 'pointer',
  borderRadius: theme.shape.borderRadius,
  marginBottom: theme.spacing(0.25),
  '&:hover': {
    backgroundColor: alpha(theme.palette.action.hover, 0.5),
  },
}))

const AgentItem = styled(ListItem)(({ theme }) => ({
  padding: theme.spacing(0.75, 1),
  paddingLeft: theme.spacing(2),
  marginBottom: theme.spacing(0.5),
  borderRadius: theme.shape.borderRadius,
  backgroundColor: alpha(theme.palette.background.default, 0.5),
  border: `1px solid ${theme.palette.divider}`,
  cursor: 'grab',
  transition: 'all 0.15s ease',
  '&:hover': {
    backgroundColor: alpha(theme.palette.primary.main, 0.1),
    borderColor: theme.palette.primary.main,
  },
  '&:active': {
    cursor: 'grabbing',
  },
}))

const AgentIcon = styled(Box)(({ theme }) => ({
  fontSize: '1rem',
  marginRight: theme.spacing(1),
  width: 20,
  height: 20,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
}))

const DragHandle = styled(Box)(({ theme }) => ({
  color: theme.palette.text.disabled,
  display: 'flex',
  alignItems: 'center',
  marginLeft: 'auto',
}))

// localStorage key for subcategory collapse state
const SUBCATEGORY_STATE_KEY = 'agent-palette-subcategories'

function AgentPalette({ isCollapsed = false, onToggleCollapse }: AgentPaletteProps) {
  // Get agent icons from registry metadata
  const { agents: agentMetadata } = useAgentMetadata()

  const [agents, setAgents] = useState<AgentInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')

  // Track which subcategories are expanded (default: all expanded)
  const [expandedSubcategories, setExpandedSubcategories] = useState<Record<string, boolean>>(() => {
    try {
      const stored = localStorage.getItem(SUBCATEGORY_STATE_KEY)
      if (stored) {
        return JSON.parse(stored)
      }
    } catch {
      // Ignore localStorage errors
    }
    // Default: all expanded
    return SUBCATEGORY_ORDER.reduce((acc, sub) => ({ ...acc, [sub]: true }), {})
  })

  // Persist subcategory state
  useEffect(() => {
    try {
      localStorage.setItem(SUBCATEGORY_STATE_KEY, JSON.stringify(expandedSubcategories))
    } catch {
      // Ignore localStorage errors
    }
  }, [expandedSubcategories])

  // Fetch agents on mount
  useEffect(() => {
    async function loadAgents() {
      setLoading(true)
      setError(null)

      try {
        const catalog = await fetchPromptCatalog()
        // Filter out Routing category and flatten to agent list
        // Map the catalog agents to our AgentInfo type
        const filteredAgents: AgentInfo[] = catalog.categories
          .filter((cat) => cat.category !== 'Routing')
          .flatMap((cat) =>
            cat.agents.map((agent) => ({
              agent_id: agent.agent_id,
              agent_name: agent.agent_name,
              description: agent.description,
              category: cat.category,
              subcategory: agent.subcategory,
              has_mod_rules: agent.has_mod_rules,
              tools: agent.tools,
            }))
          )

        setAgents(filteredAgents)
      } catch (err) {
        logger.error('Failed to load agents', err as Error, { component: 'AgentPalette' })
        setError('Failed to load agents')
      } finally {
        setLoading(false)
      }
    }

    loadAgents()
  }, [])

  // Filter agents based on search query
  const filteredAgents = useMemo(() => {
    if (!searchQuery.trim()) return agents

    const query = searchQuery.toLowerCase()
    return agents.filter((agent) => {
      const matchesName = agent.agent_name.toLowerCase().includes(query)
      const matchesDescription = agent.description.toLowerCase().includes(query)
      const matchesTools = agent.tools?.some((t) => t.toLowerCase().includes(query)) || false
      return matchesName || matchesDescription || matchesTools
    })
  }, [agents, searchQuery])

  // Group filtered agents by subcategory in the defined order
  const agentsBySubcategory = useMemo(() => {
    const grouped: Record<string, AgentInfo[]> = {}

    // Initialize with empty arrays for known subcategories
    SUBCATEGORY_ORDER.forEach(sub => {
      grouped[sub] = []
    })

    // Group agents
    filteredAgents.forEach(agent => {
      const subcategory = agent.subcategory || agent.category || 'Other'
      if (!grouped[subcategory]) {
        grouped[subcategory] = []
      }
      grouped[subcategory].push(agent)
    })

    // Return only non-empty subcategories in order
    return Object.entries(grouped)
      .filter(([, agents]) => agents.length > 0)
      .sort(([a], [b]) => {
        const orderA = SUBCATEGORY_ORDER.indexOf(a)
        const orderB = SUBCATEGORY_ORDER.indexOf(b)
        // If both are in order array, use that order
        if (orderA !== -1 && orderB !== -1) return orderA - orderB
        // Known subcategories come first
        if (orderA !== -1) return -1
        if (orderB !== -1) return 1
        // Otherwise alphabetical
        return a.localeCompare(b)
      })
  }, [filteredAgents])

  // Toggle subcategory expansion
  const toggleSubcategory = (subcategory: string) => {
    setExpandedSubcategories(prev => ({
      ...prev,
      [subcategory]: !prev[subcategory]
    }))
  }

  // Clear search
  const handleClearSearch = () => {
    setSearchQuery('')
  }

  // Handle drag start - pass agent data
  const handleDragStart = (event: React.DragEvent, agent: AgentInfo) => {
    // Use 'task_input' node type for Initial Instructions, 'agent' for all others
    const nodeType = agent.agent_id === 'task_input' ? 'task_input' : 'agent'
    event.dataTransfer.setData('application/reactflow', JSON.stringify({
      type: nodeType,
      agentId: agent.agent_id,
      agentName: agent.agent_name,
      agentDescription: agent.description,
    }))
    event.dataTransfer.effectAllowed = 'move'
  }

  return (
    <PaletteContainer elevation={2}>
      <PaletteHeader>
        <Typography variant="subtitle2" sx={{ fontWeight: 600, fontSize: '0.8rem' }}>
          Agent Palette
        </Typography>
        {onToggleCollapse && (
          <IconButton size="small" onClick={onToggleCollapse}>
            {isCollapsed ? <ExpandMoreIcon fontSize="small" /> : <ExpandLessIcon fontSize="small" />}
          </IconButton>
        )}
      </PaletteHeader>

      {!isCollapsed && (
        <>
          <SearchBox>
            <TextField
              fullWidth
              size="small"
              placeholder="Search agents..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchIcon sx={{ fontSize: '1rem' }} color="action" />
                  </InputAdornment>
                ),
                endAdornment: searchQuery && (
                  <InputAdornment position="end">
                    <IconButton size="small" onClick={handleClearSearch} edge="end">
                      <ClearIcon sx={{ fontSize: '0.9rem' }} />
                    </IconButton>
                  </InputAdornment>
                ),
                sx: { fontSize: '0.75rem' },
              }}
              sx={{
                '& .MuiInputBase-root': {
                  height: 28,
                },
                '& .MuiInputBase-input': {
                  fontSize: '0.75rem',
                  py: 0.5,
                },
              }}
            />
          </SearchBox>
          <PaletteContent>
            {loading && (
            <Box sx={{ display: 'flex', justifyContent: 'center', p: 2 }}>
              <CircularProgress size={24} />
            </Box>
          )}

          {error && (
            <Alert severity="error" sx={{ m: 1 }}>
              {error}
            </Alert>
          )}

          {!loading && !error && agentsBySubcategory.length > 0 && (
            <Box>
              {agentsBySubcategory.map(([subcategory, subcategoryAgents]) => (
                <Box key={subcategory} sx={{ mb: 0.5 }}>
                  <SubcategoryHeader onClick={() => toggleSubcategory(subcategory)}>
                    {expandedSubcategories[subcategory] ? (
                      <KeyboardArrowDownIcon sx={{ fontSize: '1rem', color: 'text.secondary', mr: 0.5 }} />
                    ) : (
                      <KeyboardArrowRightIcon sx={{ fontSize: '1rem', color: 'text.secondary', mr: 0.5 }} />
                    )}
                    <Typography
                      variant="caption"
                      sx={{
                        fontWeight: 600,
                        color: 'text.secondary',
                        textTransform: 'uppercase',
                        fontSize: '0.65rem',
                        letterSpacing: '0.05em',
                      }}
                    >
                      {subcategory}
                    </Typography>
                    <Typography
                      variant="caption"
                      sx={{ ml: 0.5, color: 'text.disabled', fontSize: '0.6rem' }}
                    >
                      ({subcategoryAgents.length})
                    </Typography>
                  </SubcategoryHeader>

                  <Collapse in={expandedSubcategories[subcategory] !== false}>
                    <List dense disablePadding>
                      {subcategoryAgents.map((agent) => (
                        <Tooltip
                          key={agent.agent_id}
                          title={agent.description}
                          placement="right"
                          enterDelay={500}
                        >
                          <AgentItem
                            draggable
                            onDragStart={(e) => handleDragStart(e, agent)}
                          >
                            <AgentIcon>{agentMetadata[agent.agent_id]?.icon || '✨'}</AgentIcon>
                            <Typography variant="body2" sx={{ fontSize: '0.75rem', flex: 1 }}>
                              {agent.agent_name}
                            </Typography>
                            <DragHandle>
                              <DragIndicatorIcon fontSize="small" sx={{ fontSize: '1rem' }} />
                            </DragHandle>
                          </AgentItem>
                        </Tooltip>
                      ))}
                    </List>
                  </Collapse>
                </Box>
              ))}
            </Box>
          )}

          {!loading && !error && searchQuery && filteredAgents.length === 0 && (
            <Box sx={{ p: 1.5, textAlign: 'center' }}>
              <Typography variant="caption" color="text.secondary">
                No agents match "{searchQuery}"
              </Typography>
            </Box>
          )}

          {!loading && !error && agents.length === 0 && (
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ textAlign: 'center', p: 2 }}
            >
              No agents available
            </Typography>
          )}
        </PaletteContent>
        </>
      )}
    </PaletteContainer>
  )
}

export default AgentPalette
