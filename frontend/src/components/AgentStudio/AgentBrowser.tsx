/**
 * AgentBrowser Component
 *
 * A fixed-width agent list (All/Shared/Templates, search, category
 * accordions with counts) beside the agent detail panel. Below the narrow
 * threshold the list is hidden and the detail shows a Back to Agents control.
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
  Stack,
  Tabs,
  Tab,
} from '@mui/material'
import { styled } from '@mui/material/styles'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import SearchIcon from '@mui/icons-material/Search'
import ClearIcon from '@mui/icons-material/Clear'

import AgentDetailsPanel from './AgentDetailsPanel'
import type { PromptCatalog, PromptInfo } from '@/types/promptExplorer'
import { useAgentMetadata } from '@/contexts/AgentMetadataContext'
import { CountPill, NARROW_BROWSER_WIDTH } from './agentGuidePrimitives'
import { useContainerWidth } from './useContainerWidth'

// Define the display order for subcategories (matching Flow Builder)
// System is added for Supervisor (not shown in Flow Builder)
const SUBCATEGORY_ORDER = ['System', 'Input', 'PDF Extraction', 'Data Validation', 'Output', 'My Custom Agents', 'Shared Agents']

const LIST_WIDTH = 260

const BrowserContainer = styled(Box)(({ theme }) => ({
  display: 'flex',
  height: '100%',
  minWidth: 0,
  minHeight: 0,
  backgroundColor: theme.palette.background.paper,
  borderRadius: theme.shape.borderRadius,
  overflow: 'hidden',
}))

const AgentListContainer = styled(Box)(({ theme }) => ({
  width: LIST_WIDTH,
  flex: 'none',
  borderRight: `1px solid ${theme.palette.divider}`,
  display: 'flex',
  flexDirection: 'column',
  minHeight: 0,
  overflow: 'hidden',
}))

const ListTop = styled(Box)(({ theme }) => ({
  padding: theme.spacing(1.25, 1.5, 1),
  display: 'flex',
  flexDirection: 'column',
  gap: theme.spacing(1),
  borderBottom: `1px solid ${theme.palette.divider}`,
}))

const AgentList = styled(Box)(({ theme }) => ({
  flex: 1,
  overflow: 'auto',
  padding: theme.spacing(0.75, 0),
}))

const DetailsContainer = styled(Box)(() => ({
  flex: 1,
  overflow: 'hidden',
  minWidth: 0,
  minHeight: 0,
}))

interface AgentBrowserProps {
  catalog: PromptCatalog
  selectedAgentId: string | null
  selectedGroupId: string | null
  onAgentSelect: (agentId: string) => void
  onGroupSelect: (groupId: string | null) => void
  onDiscussWithClaude?: (agentId: string, agentName: string, prompt?: string) => void
  onCloneToWorkshop?: (agentId: string) => void
}

type BrowserFilter = 'all' | 'shared' | 'templates'
type NarrowView = 'list' | 'detail'

function AgentBrowser({
  catalog,
  selectedAgentId,
  selectedGroupId,
  onAgentSelect,
  onGroupSelect,
  onDiscussWithClaude,
  onCloneToWorkshop,
}: AgentBrowserProps) {
  const { agents: agentMetadata } = useAgentMetadata()
  const [expandedCategories, setExpandedCategories] = useState<string[]>([])
  const [searchQuery, setSearchQuery] = useState('')
  const [browserFilter, setBrowserFilter] = useState<BrowserFilter>('all')
  const [narrowView, setNarrowView] = useState<NarrowView>('detail')
  const [containerRef, containerWidth] = useContainerWidth<HTMLDivElement>()
  const narrow = containerWidth !== null && containerWidth > 0 && containerWidth < NARROW_BROWSER_WIDTH

  // Flatten all agents from catalog
  const allAgents = useMemo(() => {
    return catalog.categories.flatMap((cat) => cat.agents)
  }, [catalog])

  // Find the selected agent
  const selectedAgent = useMemo(() => {
    if (!selectedAgentId) return null
    return allAgents.find((a) => a.agent_id === selectedAgentId) || null
  }, [allAgents, selectedAgentId])

  // The catalog carries category on the group, not on each agent
  const selectedAgentCategory = useMemo(() => {
    if (!selectedAgentId) return undefined
    return catalog.categories.find((cat) => cat.agents.some((a) => a.agent_id === selectedAgentId))?.category
  }, [catalog, selectedAgentId])

  const filterCounts = useMemo(() => {
    const shared = allAgents.filter((agent) => agent.subcategory === 'Shared Agents').length
    const templates = allAgents.filter((agent) => !agent.agent_id.startsWith('ca_')).length
    return {
      all: allAgents.length,
      shared,
      templates,
    }
  }, [allAgents])

  const tabFilteredAgents = useMemo(() => {
    if (browserFilter === 'shared') {
      return allAgents.filter((agent) => agent.subcategory === 'Shared Agents')
    }
    if (browserFilter === 'templates') {
      return allAgents.filter((agent) => !agent.agent_id.startsWith('ca_'))
    }
    return allAgents
  }, [allAgents, browserFilter])

  // Filter agents based on tab + search query
  const filteredAgents = useMemo(() => {
    if (!searchQuery.trim()) return tabFilteredAgents

    const query = searchQuery.toLowerCase()
    return tabFilteredAgents.filter((agent) => {
      const matchesName = agent.agent_name.toLowerCase().includes(query)
      const matchesDescription = agent.description.toLowerCase().includes(query)
      const matchesTools = agent.tools.some((t) => t.toLowerCase().includes(query))
      const matchesDocSummary = agent.documentation?.summary?.toLowerCase().includes(query) || false

      return matchesName || matchesDescription || matchesTools || matchesDocSummary
    })
  }, [searchQuery, tabFilteredAgents])

  // Group filtered agents by subcategory in the defined order
  const agentsBySubcategory = useMemo(() => {
    const grouped: Record<string, PromptInfo[]> = {}

    SUBCATEGORY_ORDER.forEach((sub) => {
      grouped[sub] = []
    })

    filteredAgents.forEach((agent) => {
      const subcategory = agent.subcategory || 'Other'
      if (!grouped[subcategory]) {
        grouped[subcategory] = []
      }
      grouped[subcategory].push(agent)
    })

    return Object.entries(grouped)
      .filter(([, agents]) => agents.length > 0)
      .sort(([a], [b]) => {
        const orderA = SUBCATEGORY_ORDER.indexOf(a)
        const orderB = SUBCATEGORY_ORDER.indexOf(b)
        if (orderA !== -1 && orderB !== -1) return orderA - orderB
        if (orderA !== -1) return -1
        if (orderB !== -1) return 1
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
        setExpandedCategories((prev) => (
          prev.includes(subcategory) ? prev : [...prev, subcategory]
        ))
      }
    }
  }, [selectedAgentId, allAgents])

  const handleCategoryToggle = (category: string) => {
    setExpandedCategories((prev) =>
      prev.includes(category) ? prev.filter((c) => c !== category) : [...prev, category]
    )
  }

  const handleAgentSelect = (agentId: string) => {
    setNarrowView('detail')
    onAgentSelect(agentId)
  }

  const handleClearSearch = () => {
    setSearchQuery('')
  }

  const showList = !narrow || narrowView === 'list' || !selectedAgent
  const showDetail = !narrow || !showList

  return (
    <BrowserContainer ref={containerRef} data-narrow={narrow ? 'true' : undefined}>
      {showList && (
        <AgentListContainer sx={narrow ? { width: '100%', borderRight: 0 } : undefined}>
          <ListTop>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <Typography component="h2" sx={{ m: 0, fontSize: 15, fontWeight: 600 }}>
                Agents
              </Typography>
              <CountPill label={`${filteredAgents.length} agents shown`}>
                {filteredAgents.length}{searchQuery ? ` / ${tabFilteredAgents.length}` : ''}
              </CountPill>
            </Box>
            <Tabs
              value={browserFilter}
              onChange={(_event, nextValue) => setBrowserFilter(nextValue as BrowserFilter)}
              aria-label="Agent list filter"
              sx={{ minHeight: 26, '& .MuiTabs-indicator': { height: 2 } }}
            >
              {([
                ['all', `All (${filterCounts.all})`],
                ['shared', `Shared (${filterCounts.shared})`],
                ['templates', `Templates (${filterCounts.templates})`],
              ] as const).map(([value, label]) => (
                <Tab
                  key={value}
                  value={value}
                  label={label}
                  sx={{ minHeight: 26, minWidth: 0, px: 0.75, py: 0.25, fontSize: 12, textTransform: 'none' }}
                />
              ))}
            </Tabs>
            <TextField
              fullWidth
              size="small"
              placeholder="Search agents..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              inputProps={{ 'aria-label': 'Search agents' }}
              sx={{
                '& .MuiInputBase-root': { height: 30, fontSize: 12.5, backgroundColor: 'background.default' },
                '& .MuiInputBase-input': { py: 0 },
              }}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchIcon sx={{ fontSize: 16 }} color="action" />
                  </InputAdornment>
                ),
                endAdornment: searchQuery && (
                  <InputAdornment position="end">
                    <IconButton size="small" onClick={handleClearSearch} edge="end" aria-label="Clear search">
                      <ClearIcon sx={{ fontSize: 16 }} />
                    </IconButton>
                  </InputAdornment>
                ),
              }}
            />
          </ListTop>
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
                  expandIcon={<ExpandMoreIcon sx={{ fontSize: 18 }} />}
                  sx={{
                    minHeight: 30,
                    px: 1.5,
                    flexDirection: 'row-reverse',
                    gap: 0.75,
                    '& .MuiAccordionSummary-content': { my: 0.5, minWidth: 0 },
                    '& .MuiAccordionSummary-expandIconWrapper': { transform: 'rotate(-90deg)' },
                    '& .MuiAccordionSummary-expandIconWrapper.Mui-expanded': { transform: 'rotate(0deg)' },
                  }}
                >
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, minWidth: 0 }}>
                    <Typography
                      component="span"
                      sx={{
                        fontSize: 11,
                        letterSpacing: '0.06em',
                        textTransform: 'uppercase',
                        color: 'text.secondary',
                        fontWeight: 500,
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {subcategory}
                    </Typography>
                    <CountPill>{subcategoryAgents.length}</CountPill>
                    {subcategory === 'System' && (
                      <Tooltip title="Works behind the scenes - not available in Flow Builder">
                        <Chip
                          size="small"
                          label="internal"
                          sx={{ height: 16, fontSize: 10, backgroundColor: 'action.selected' }}
                        />
                      </Tooltip>
                    )}
                  </Box>
                </AccordionSummary>
                <AccordionDetails sx={{ p: 0 }}>
                  <List dense disablePadding>
                    {subcategoryAgents.map((agent) => {
                      const restrictedTo = agentMetadata[agent.agent_id]?.allowed_group_ids || []
                      const selected = selectedAgentId === agent.agent_id
                      return (
                        <ListItemButton
                          key={agent.agent_id}
                          selected={selected}
                          onClick={() => handleAgentSelect(agent.agent_id)}
                          sx={{
                            pl: 2.5,
                            pr: 1.5,
                            py: 0.75,
                            minHeight: 0,
                            '&.Mui-selected': {
                              backgroundColor: 'action.selected',
                              boxShadow: (theme) => `inset 3px 0 0 ${theme.palette.primary.main}`,
                            },
                          }}
                        >
                          <ListItemText
                            disableTypography
                            primary={(
                              <Stack direction="row" spacing={0.75} alignItems="center" sx={{ minWidth: 0 }}>
                                <Box
                                  component="span"
                                  title={agent.agent_name}
                                  sx={{
                                    fontSize: 13,
                                    fontWeight: selected ? 500 : 400,
                                    overflow: 'hidden',
                                    textOverflow: 'ellipsis',
                                    whiteSpace: 'nowrap',
                                    minWidth: 0,
                                  }}
                                >
                                  {agent.agent_name}
                                </Box>
                                {restrictedTo.length > 0 && (
                                  <Chip
                                    size="small"
                                    color="warning"
                                    variant="outlined"
                                    label={`Restricted: ${restrictedTo.join(', ')}`}
                                    aria-label={`${agent.agent_name} restricted to ${restrictedTo.join(', ')}`}
                                    sx={{ height: 18, fontSize: '0.65rem', flex: 'none' }}
                                  />
                                )}
                              </Stack>
                            )}
                            secondary={agent.has_group_rules ? (
                              <Box component="span" sx={{ display: 'block', fontSize: 11, color: 'text.secondary' }}>
                                Has group rules
                              </Box>
                            ) : undefined}
                            sx={{ m: 0, minWidth: 0 }}
                          />
                        </ListItemButton>
                      )
                    })}
                  </List>
                </AccordionDetails>
              </Accordion>
            ))}
            {searchQuery && filteredAgents.length === 0 && (
              <Box sx={{ p: 2, textAlign: 'center' }}>
                <Typography variant="body2" color="text.secondary">
                  No agents match: {searchQuery}
                </Typography>
              </Box>
            )}
          </AgentList>
        </AgentListContainer>
      )}

      {showDetail && (
        <DetailsContainer>
          <AgentDetailsPanel
            agent={selectedAgent}
            category={selectedAgentCategory}
            selectedGroupId={selectedGroupId}
            onGroupSelect={onGroupSelect}
            onDiscussWithClaude={onDiscussWithClaude}
            onCloneToWorkshop={onCloneToWorkshop}
            onBack={narrow ? () => setNarrowView('list') : undefined}
            narrow={narrow}
          />
        </DetailsContainer>
      )}
    </BrowserContainer>
  )
}

export default AgentBrowser
