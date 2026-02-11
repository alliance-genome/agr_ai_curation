/**
 * AgentBrowser Component
 *
 * Displays agents organized by subcategory (matching Flow Builder palette) with:
 * - Search/filter box for finding agents
 * - Collapsible subcategory sections
 * - Agent selection via AgentDetailsPanel
 *
 * Categories mirror Flow Builder:
 * - System (Supervisor only - not in Flow Builder)
 * - Input
 * - PDF Extraction
 * - Data Validation
 * - Output
 */

import { useState, useEffect, useMemo, useRef } from 'react'
import {
  Box,
  Typography,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  List,
  ListItemButton,
  ListItemText,
  Chip,
  Tooltip,
  IconButton,
  TextField,
  InputAdornment,
} from '@mui/material'
import { styled } from '@mui/material/styles'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import SearchIcon from '@mui/icons-material/Search'
import ClearIcon from '@mui/icons-material/Clear'
import SettingsIcon from '@mui/icons-material/Settings'
import InputIcon from '@mui/icons-material/Input'
import PictureAsPdfIcon from '@mui/icons-material/PictureAsPdf'
import FactCheckIcon from '@mui/icons-material/FactCheck'
import OutputIcon from '@mui/icons-material/Output'
import ScienceIcon from '@mui/icons-material/Science'

import AgentDetailsPanel from './AgentDetailsPanel'
import type { PromptCatalog, PromptInfo } from '@/types/promptExplorer'

// Define the display order for subcategories (matching Flow Builder)
// System is added for Supervisor (not shown in Flow Builder)
const SUBCATEGORY_ORDER = ['System', 'Input', 'PDF Extraction', 'Data Validation', 'Output', 'My Custom Agents']

// Map subcategories to their display icons
const SubcategoryIcon: Record<string, JSX.Element> = {
  System: <SettingsIcon fontSize="small" />,
  Input: <InputIcon fontSize="small" />,
  'PDF Extraction': <PictureAsPdfIcon fontSize="small" />,
  'Data Validation': <FactCheckIcon fontSize="small" />,
  Output: <OutputIcon fontSize="small" />,
  'My Custom Agents': <ScienceIcon fontSize="small" />,
}

const BrowserContainer = styled(Box)(({ theme }) => ({
  display: 'flex',
  flexDirection: 'column',
  height: '100%',
  backgroundColor: theme.palette.background.paper,
  borderRadius: theme.shape.borderRadius,
  overflow: 'hidden',
}))

const BrowserHeader = styled(Box)(({ theme }) => ({
  padding: theme.spacing(2),
  borderBottom: `1px solid ${theme.palette.divider}`,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
}))

const ContentArea = styled(Box)(() => ({
  flex: 1,
  display: 'flex',
  minHeight: 0,
  overflow: 'hidden',
}))

const AgentListContainer = styled(Box)(({ theme }) => ({
  width: 280,
  borderRight: `1px solid ${theme.palette.divider}`,
  display: 'flex',
  flexDirection: 'column',
  overflow: 'hidden',
}))

const SearchBox = styled(Box)(({ theme }) => ({
  padding: theme.spacing(1.5),
  borderBottom: `1px solid ${theme.palette.divider}`,
}))

const AgentList = styled(Box)(() => ({
  flex: 1,
  overflow: 'auto',
}))

const DetailsContainer = styled(Box)(() => ({
  flex: 1,
  overflow: 'hidden',
  minWidth: 0,
}))

interface AgentBrowserProps {
  catalog: PromptCatalog
  selectedAgentId: string | null
  selectedModId: string | null
  viewMode: 'base' | 'mod' | 'combined'
  onAgentSelect: (agentId: string) => void
  onModSelect: (modId: string | null) => void
  onViewModeChange: (mode: 'base' | 'mod' | 'combined') => void
  onDiscussWithClaude?: (agentId: string, agentName: string) => void
  onCloneToWorkshop?: (agentId: string) => void
}

function AgentBrowser({
  catalog,
  selectedAgentId,
  selectedModId,
  viewMode,
  onAgentSelect,
  onModSelect,
  onViewModeChange,
  onDiscussWithClaude,
  onCloneToWorkshop,
}: AgentBrowserProps) {
  const [expandedCategories, setExpandedCategories] = useState<string[]>([])
  const [searchQuery, setSearchQuery] = useState('')

  // Flatten all agents from catalog
  const allAgents = useMemo(() => {
    return catalog.categories.flatMap((cat) => cat.agents)
  }, [catalog])

  // Find the selected agent
  const selectedAgent = useMemo(() => {
    if (!selectedAgentId) return null
    return allAgents.find((a) => a.agent_id === selectedAgentId) || null
  }, [allAgents, selectedAgentId])

  // Filter agents based on search query
  const filteredAgents = useMemo(() => {
    if (!searchQuery.trim()) return allAgents

    const query = searchQuery.toLowerCase()
    return allAgents.filter((agent) => {
      // Match against name, description, tools, and documentation
      const matchesName = agent.agent_name.toLowerCase().includes(query)
      const matchesDescription = agent.description.toLowerCase().includes(query)
      const matchesTools = agent.tools.some((t) => t.toLowerCase().includes(query))
      // Also check documentation if available
      const matchesDocSummary = agent.documentation?.summary?.toLowerCase().includes(query) || false

      return matchesName || matchesDescription || matchesTools || matchesDocSummary
    })
  }, [allAgents, searchQuery])

  // Group filtered agents by subcategory in the defined order
  const agentsBySubcategory = useMemo(() => {
    const grouped: Record<string, PromptInfo[]> = {}

    // Initialize with empty arrays for known subcategories
    SUBCATEGORY_ORDER.forEach((sub) => {
      grouped[sub] = []
    })

    // Group agents by subcategory
    filteredAgents.forEach((agent) => {
      const subcategory = agent.subcategory || 'Other'
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

  // Auto-expand category when selecting a new agent
  const prevAgentIdRef = useRef<string | null>(null)
  useEffect(() => {
    if (selectedAgentId && selectedAgentId !== prevAgentIdRef.current) {
      prevAgentIdRef.current = selectedAgentId
      const agent = allAgents.find((a) => a.agent_id === selectedAgentId)
      if (agent) {
        const subcategory = agent.subcategory || 'Other'
        if (!expandedCategories.includes(subcategory)) {
          setExpandedCategories((prev) => [...prev, subcategory])
        }
      }
    }
  }, [selectedAgentId, allAgents])

  // Toggle category expansion
  const handleCategoryToggle = (category: string) => {
    setExpandedCategories((prev) =>
      prev.includes(category) ? prev.filter((c) => c !== category) : [...prev, category]
    )
  }

  // Clear search
  const handleClearSearch = () => {
    setSearchQuery('')
  }

  return (
    <BrowserContainer>
      <BrowserHeader>
        <Typography variant="h6" sx={{ fontWeight: 500 }}>
          Agent Browser
        </Typography>
        <Chip
          size="small"
          label={`${filteredAgents.length}${searchQuery ? ` / ${allAgents.length}` : ''} agents`}
          variant="outlined"
        />
      </BrowserHeader>

      <ContentArea>
        {/* Agent List with Search */}
        <AgentListContainer>
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
                    <SearchIcon fontSize="small" color="action" />
                  </InputAdornment>
                ),
                endAdornment: searchQuery && (
                  <InputAdornment position="end">
                    <IconButton size="small" onClick={handleClearSearch} edge="end">
                      <ClearIcon fontSize="small" />
                    </IconButton>
                  </InputAdornment>
                ),
              }}
            />
          </SearchBox>
          <AgentList>
            {agentsBySubcategory.map(([subcategory, subcategoryAgents]) => (
              <Accordion
                key={subcategory}
                expanded={expandedCategories.includes(subcategory)}
                onChange={() => handleCategoryToggle(subcategory)}
                disableGutters
                elevation={0}
                sx={{
                  '&:before': { display: 'none' },
                  backgroundColor: 'transparent',
                }}
              >
                <AccordionSummary
                  expandIcon={<ExpandMoreIcon />}
                  sx={{
                    minHeight: 40,
                    '& .MuiAccordionSummary-content': { my: 0.5 },
                  }}
                >
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    {SubcategoryIcon[subcategory] || <FactCheckIcon fontSize="small" />}
                    <Typography variant="subtitle2">{subcategory}</Typography>
                    <Chip size="small" label={subcategoryAgents.length} sx={{ height: 18 }} />
                    {subcategory === 'System' && (
                      <Tooltip title="Works behind the scenes - not available in Flow Builder">
                        <Chip
                          size="small"
                          label="internal"
                          sx={{
                            height: 16,
                            fontSize: '0.6rem',
                            backgroundColor: 'action.selected',
                          }}
                        />
                      </Tooltip>
                    )}
                  </Box>
                </AccordionSummary>
                <AccordionDetails sx={{ p: 0 }}>
                  <List dense disablePadding>
                    {subcategoryAgents.map((agent) => (
                      <ListItemButton
                        key={agent.agent_id}
                        selected={selectedAgentId === agent.agent_id}
                        onClick={() => onAgentSelect(agent.agent_id)}
                        sx={{ pl: 4 }}
                      >
                        <ListItemText
                          primary={agent.agent_name}
                          secondary={agent.has_mod_rules ? 'Has MOD rules' : undefined}
                          primaryTypographyProps={{ variant: 'body2' }}
                          secondaryTypographyProps={{ variant: 'caption' }}
                        />
                      </ListItemButton>
                    ))}
                  </List>
                </AccordionDetails>
              </Accordion>
            ))}
            {searchQuery && filteredAgents.length === 0 && (
              <Box sx={{ p: 2, textAlign: 'center' }}>
                <Typography variant="body2" color="text.secondary">
                  No agents match "{searchQuery}"
                </Typography>
              </Box>
            )}
          </AgentList>
        </AgentListContainer>

        {/* Agent Details Panel */}
        <DetailsContainer>
          <AgentDetailsPanel
            agent={selectedAgent}
            selectedModId={selectedModId}
            viewMode={viewMode}
            onModSelect={onModSelect}
            onViewModeChange={onViewModeChange}
            onDiscussWithClaude={onDiscussWithClaude}
            onCloneToWorkshop={onCloneToWorkshop}
          />
        </DetailsContainer>
      </ContentArea>
    </BrowserContainer>
  )
}

export default AgentBrowser
