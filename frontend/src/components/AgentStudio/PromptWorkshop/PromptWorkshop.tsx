import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Checkbox,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControl,
  FormControlLabel,
  IconButton,
  InputAdornment,
  InputLabel,
  List,
  ListItem,
  ListItemButton,
  ListItemText,
  Menu,
  MenuItem,
  Paper,
  Select,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import { styled, alpha } from '@mui/material/styles'
import SearchIcon from '@mui/icons-material/Search'
import DescriptionOutlinedIcon from '@mui/icons-material/DescriptionOutlined'
import DeleteIcon from '@mui/icons-material/Delete'

import type {
  PromptCatalog,
  PromptInfo,
  CustomAgent,
  CustomAgentVersion,
  PromptWorkshopContext,
} from '@/types/promptExplorer'
import {
  createCustomAgent,
  deleteCustomAgent,
  listCustomAgentVersions,
  listCustomAgents,
  revertCustomAgentVersion,
  updateCustomAgent,
} from '@/services/agentStudioService'
import { useAgentMetadata } from '@/contexts/AgentMetadataContext'

const FALLBACK_ICON_OPTIONS = ['🔧', '🧬', '📄', '🔍', '🧪', '📊', '🧠', '⚙️', '✨', '📝', '📚', '🧩']

const Toolbar = styled(Box)(({ theme }) => ({
  display: 'flex',
  alignItems: 'center',
  height: 32,
  padding: theme.spacing(0, 0.5),
  borderBottom: `1px solid ${theme.palette.divider}`,
  backgroundColor: alpha(theme.palette.background.default, 0.4),
  gap: theme.spacing(0.25),
}))

const MenuTrigger = styled(Box)(({ theme }) => ({
  display: 'inline-flex',
  alignItems: 'center',
  padding: theme.spacing(0.25, 1),
  fontSize: '0.8rem',
  fontWeight: 500,
  cursor: 'pointer',
  borderRadius: 3,
  color: theme.palette.text.secondary,
  transition: 'all 0.1s ease',
  userSelect: 'none',
  '&:hover': {
    backgroundColor: alpha(theme.palette.action.hover, 0.8),
    color: theme.palette.text.primary,
  },
}))

const StyledMenu = styled(Menu)(({ theme }) => ({
  '& .MuiPaper-root': {
    minWidth: 220,
    backgroundColor: theme.palette.background.paper,
    border: `1px solid ${theme.palette.divider}`,
    boxShadow: '0 4px 20px rgba(0,0,0,0.3)',
    borderRadius: 6,
    marginTop: 2,
  },
  '& .MuiList-root': {
    padding: theme.spacing(0.5, 0),
  },
}))

const StyledMenuItem = styled(MenuItem)(({ theme }) => ({
  padding: theme.spacing(0.5, 1.5),
  minHeight: 28,
  fontSize: '0.8rem',
  display: 'flex',
  justifyContent: 'space-between',
  gap: theme.spacing(3),
  '&:hover': {
    backgroundColor: alpha(theme.palette.primary.main, 0.12),
  },
  '&.Mui-disabled': {
    opacity: 0.4,
  },
}))

interface PromptWorkshopProps {
  catalog: PromptCatalog
  initialParentAgentId?: string | null
  onContextChange?: (context: PromptWorkshopContext) => void
}

function PromptWorkshop({ catalog, initialParentAgentId, onContextChange }: PromptWorkshopProps) {
  const { agents: agentMetadata, refresh: refreshAgentMetadata } = useAgentMetadata()

  const [parentAgentId, setParentAgentId] = useState('')
  const [customAgents, setCustomAgents] = useState<CustomAgent[]>([])
  const [selectedCustomAgentId, setSelectedCustomAgentId] = useState<string>('')
  const [versions, setVersions] = useState<CustomAgentVersion[]>([])
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [status, setStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [customPrompt, setCustomPrompt] = useState('')
  const [debouncedPromptDraft, setDebouncedPromptDraft] = useState('')
  const [modPromptOverrides, setModPromptOverrides] = useState<Record<string, string>>({})
  const [debouncedModPromptOverrides, setDebouncedModPromptOverrides] = useState<Record<string, string>>({})
  const [includeModRules, setIncludeModRules] = useState(true)
  const [icon, setIcon] = useState('🔧')
  const [saveNotes, setSaveNotes] = useState('')
  const [modId, setModId] = useState('')

  const [fileMenuAnchor, setFileMenuAnchor] = useState<HTMLElement | null>(null)
  const [openDialogOpen, setOpenDialogOpen] = useState(false)
  const [openSearchTerm, setOpenSearchTerm] = useState('')
  const [manageDialogOpen, setManageDialogOpen] = useState(false)
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [pendingDeleteAgent, setPendingDeleteAgent] = useState<CustomAgent | null>(null)

  const parentAgents = useMemo(() => {
    const seen = new Set<string>()
    const agents: PromptInfo[] = []
    for (const category of catalog.categories) {
      for (const agent of category.agents) {
        if (agent.agent_id === 'task_input') continue
        if (agent.agent_id.startsWith('ca_')) continue
        if (seen.has(agent.agent_id)) continue
        seen.add(agent.agent_id)
        agents.push(agent)
      }
    }
    return agents.sort((a, b) => a.agent_name.localeCompare(b.agent_name))
  }, [catalog])

  const parentAgent = useMemo(
    () => parentAgents.find((agent) => agent.agent_id === parentAgentId),
    [parentAgents, parentAgentId]
  )

  const selectedCustomAgent = useMemo(
    () => customAgents.find((agent) => agent.id === selectedCustomAgentId),
    [customAgents, selectedCustomAgentId]
  )

  const availableModIds = useMemo(
    () => Object.keys(parentAgent?.mod_rules || {}).sort(),
    [parentAgent]
  )

  const selectedModId = useMemo(() => modId.trim().toUpperCase(), [modId])

  const selectedModBasePrompt = useMemo(() => {
    if (!selectedModId) return ''
    return parentAgent?.mod_rules[selectedModId]?.content || ''
  }, [parentAgent, selectedModId])

  const selectedModPrompt = useMemo(() => {
    if (!selectedModId) return ''
    if (Object.prototype.hasOwnProperty.call(modPromptOverrides, selectedModId)) {
      return modPromptOverrides[selectedModId]
    }
    return selectedModBasePrompt
  }, [modPromptOverrides, selectedModId, selectedModBasePrompt])

  const selectedModPromptForContext = useMemo(() => {
    if (!selectedModId) return undefined
    if (Object.prototype.hasOwnProperty.call(debouncedModPromptOverrides, selectedModId)) {
      return debouncedModPromptOverrides[selectedModId]
    }
    return parentAgent?.mod_rules[selectedModId]?.content
  }, [debouncedModPromptOverrides, parentAgent, selectedModId])

  const hasSelectedModOverride = useMemo(
    () => Boolean(selectedModId && Object.prototype.hasOwnProperty.call(modPromptOverrides, selectedModId)),
    [modPromptOverrides, selectedModId]
  )

  const hasAnyModOverrides = useMemo(
    () => Object.keys(modPromptOverrides).length > 0,
    [modPromptOverrides]
  )

  const iconOptions = useMemo(() => {
    const discovered = Object.values(agentMetadata)
      .map((agent) => agent.icon)
      .filter((candidate): candidate is string => Boolean(candidate && candidate.trim()))
    return Array.from(new Set([...FALLBACK_ICON_OPTIONS, ...discovered, icon || '🔧']))
  }, [agentMetadata, icon])

  const filteredOpenAgents = useMemo(() => {
    if (!openSearchTerm.trim()) return customAgents
    const query = openSearchTerm.toLowerCase()
    return customAgents.filter((agent) => {
      return agent.name.toLowerCase().includes(query) || (agent.description || '').toLowerCase().includes(query)
    })
  }, [customAgents, openSearchTerm])

  useEffect(() => {
    if (!initialParentAgentId) return
    if (parentAgents.some((agent) => agent.agent_id === initialParentAgentId)) {
      setParentAgentId(initialParentAgentId)
    }
  }, [initialParentAgentId, parentAgents])

  useEffect(() => {
    if (!parentAgentId && parentAgents.length > 0) {
      setParentAgentId(parentAgents[0].agent_id)
    }
  }, [parentAgentId, parentAgents])

  useEffect(() => {
    async function loadCustomAgents() {
      if (!parentAgentId) return
      setLoading(true)
      setError(null)
      setStatus(null)
      try {
        const response = await listCustomAgents(parentAgentId)
        setCustomAgents(response.custom_agents)

        if (response.custom_agents.length > 0) {
          setSelectedCustomAgentId((prev) => {
            const stillExists = response.custom_agents.some((agent) => agent.id === prev)
            return stillExists ? prev : response.custom_agents[0].id
          })
        } else {
          const basePrompt = parentAgent?.base_prompt || ''
          setSelectedCustomAgentId('')
          setName('')
          setDescription('')
          setCustomPrompt(basePrompt)
          setDebouncedPromptDraft(basePrompt)
          setModPromptOverrides({})
          setDebouncedModPromptOverrides({})
          setIncludeModRules(true)
          setIcon('🔧')
          setVersions([])
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load custom agents')
      } finally {
        setLoading(false)
      }
    }
    void loadCustomAgents()
  }, [parentAgentId, parentAgent?.base_prompt])

  useEffect(() => {
    async function loadVersions() {
      if (!selectedCustomAgentId) {
        setVersions([])
        return
      }
      try {
        const loaded = await listCustomAgentVersions(selectedCustomAgentId)
        setVersions(loaded)
      } catch {
        setVersions([])
      }
    }
    void loadVersions()
  }, [selectedCustomAgentId])

  useEffect(() => {
    if (!selectedCustomAgent) {
      const basePrompt = parentAgent?.base_prompt || ''
      setName('')
      setDescription('')
      setCustomPrompt(basePrompt)
      setDebouncedPromptDraft(basePrompt)
      setModPromptOverrides({})
      setDebouncedModPromptOverrides({})
      setIncludeModRules(true)
      setIcon('🔧')
      return
    }

    setName(selectedCustomAgent.name)
    setDescription(selectedCustomAgent.description || '')
    setCustomPrompt(selectedCustomAgent.custom_prompt)
    setDebouncedPromptDraft(selectedCustomAgent.custom_prompt)
    setModPromptOverrides(selectedCustomAgent.mod_prompt_overrides || {})
    setDebouncedModPromptOverrides(selectedCustomAgent.mod_prompt_overrides || {})
    setIncludeModRules(selectedCustomAgent.include_mod_rules)
    setIcon(selectedCustomAgent.icon || '🔧')
  }, [selectedCustomAgent, parentAgent?.base_prompt])

  useEffect(() => {
    if (availableModIds.length === 0) {
      if (modId) setModId('')
      return
    }
    if (!modId || !availableModIds.includes(modId)) {
      setModId(availableModIds[0])
    }
  }, [availableModIds, modId])

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      setDebouncedPromptDraft(customPrompt)
    }, 450)

    return () => {
      window.clearTimeout(timeout)
    }
  }, [customPrompt])

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      setDebouncedModPromptOverrides(modPromptOverrides)
    }, 450)

    return () => {
      window.clearTimeout(timeout)
    }
  }, [modPromptOverrides])

  useEffect(() => {
    if (!onContextChange) return
    onContextChange({
      parent_agent_id: parentAgentId || undefined,
      parent_agent_name: parentAgent?.agent_name,
      custom_agent_id: selectedCustomAgent?.agent_id,
      custom_agent_name: selectedCustomAgent?.name,
      include_mod_rules: includeModRules,
      selected_mod_id: selectedModId || undefined,
      prompt_draft: debouncedPromptDraft,
      selected_mod_prompt_draft: selectedModPromptForContext,
      mod_prompt_override_count: Object.keys(debouncedModPromptOverrides).length,
      has_mod_prompt_overrides: Object.keys(debouncedModPromptOverrides).length > 0,
      parent_prompt_stale: selectedCustomAgent?.parent_prompt_stale,
      parent_exists: selectedCustomAgent?.parent_exists,
    })
  }, [
    onContextChange,
    parentAgentId,
    parentAgent?.agent_name,
    selectedCustomAgent?.agent_id,
    selectedCustomAgent?.name,
    selectedCustomAgent?.parent_prompt_stale,
    selectedCustomAgent?.parent_exists,
    includeModRules,
    selectedModId,
    debouncedPromptDraft,
    selectedModPromptForContext,
    debouncedModPromptOverrides,
  ])

  const handleNew = () => {
    setSelectedCustomAgentId('')
    setName(parentAgent ? `${parentAgent.agent_name} (Custom)` : '')
    setDescription('')
    setCustomPrompt(parentAgent?.base_prompt || '')
    setModPromptOverrides({})
    setDebouncedModPromptOverrides({})
    setIncludeModRules(true)
    setIcon('🔧')
    setSaveNotes('')
    setStatus('Creating a new custom agent draft')
  }

  const reloadAfterSave = async (keepId?: string) => {
    const response = await listCustomAgents(parentAgentId)
    setCustomAgents(response.custom_agents)
    if (keepId) {
      setSelectedCustomAgentId(keepId)
    } else if (response.custom_agents.length > 0) {
      setSelectedCustomAgentId(response.custom_agents[0].id)
    } else {
      setSelectedCustomAgentId('')
    }
    await refreshAgentMetadata()
  }

  const handleSave = async () => {
    if (!parentAgentId) {
      setError('Please select a parent agent')
      return
    }
    if (!name.trim()) {
      setError('Please enter a custom agent name')
      return
    }
    if (!customPrompt.trim()) {
      setError('Prompt text cannot be empty')
      return
    }

    setSaving(true)
    setError(null)
    setStatus(null)

    try {
      if (selectedCustomAgentId) {
        const updated = await updateCustomAgent(selectedCustomAgentId, {
          name: name.trim(),
          description: description.trim() || undefined,
          custom_prompt: customPrompt,
          mod_prompt_overrides: modPromptOverrides,
          include_mod_rules: includeModRules,
          icon: icon || undefined,
          notes: saveNotes.trim() || undefined,
        })
        await reloadAfterSave(updated.id)
        setStatus(`Updated "${updated.name}"`)
      } else {
        const created = await createCustomAgent({
          parent_agent_id: parentAgentId,
          name: name.trim(),
          description: description.trim() || undefined,
          custom_prompt: customPrompt,
          mod_prompt_overrides: modPromptOverrides,
          include_mod_rules: includeModRules,
          icon: icon || undefined,
        })
        await reloadAfterSave(created.id)
        setStatus(`Created "${created.name}"`)
      }
      setSaveNotes('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save custom agent')
    } finally {
      setSaving(false)
    }
  }

  const handleDeleteById = async (agent: CustomAgent) => {
    setSaving(true)
    setError(null)
    try {
      await deleteCustomAgent(agent.id)
      await reloadAfterSave()
      if (selectedCustomAgentId === agent.id) {
        const hasRemaining = customAgents.some((candidate) => candidate.id !== agent.id)
        if (!hasRemaining) handleNew()
      }
      setStatus(`Deleted "${agent.name}"`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete custom agent')
    } finally {
      setSaving(false)
    }
  }

  const handleRevert = async (version: number) => {
    if (!selectedCustomAgentId) return
    setSaving(true)
    setError(null)
    try {
      const reverted = await revertCustomAgentVersion(selectedCustomAgentId, version, saveNotes || undefined)
      await reloadAfterSave(reverted.id)
      setStatus(`Reverted to version ${version}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to revert version')
    } finally {
      setSaving(false)
    }
  }

  const handleRebase = async () => {
    if (!selectedCustomAgentId) return
    setSaving(true)
    setError(null)
    try {
      const updated = await updateCustomAgent(selectedCustomAgentId, {
        rebase_parent_hash: true,
        notes: saveNotes.trim() || 'Rebased parent hash',
      })
      await reloadAfterSave(updated.id)
      setStatus('Custom agent rebased to latest parent prompt hash')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to rebase custom agent')
    } finally {
      setSaving(false)
    }
  }

  const handleSelectedModPromptChange = (value: string) => {
    if (!selectedModId) return
    setModPromptOverrides((prev) => {
      const next = { ...prev }
      if (value === selectedModBasePrompt || (!value.trim() && !selectedModBasePrompt.trim())) {
        delete next[selectedModId]
      } else {
        next[selectedModId] = value
      }
      return next
    })
  }

  const handleResetSelectedModPrompt = () => {
    if (!selectedModId) return
    setModPromptOverrides((prev) => {
      if (!Object.prototype.hasOwnProperty.call(prev, selectedModId)) return prev
      const next = { ...prev }
      delete next[selectedModId]
      return next
    })
  }

  const handleFileMenuOpen = (event: React.MouseEvent<HTMLElement>) => {
    setFileMenuAnchor(event.currentTarget)
  }

  const handleFileMenuClose = () => {
    setFileMenuAnchor(null)
  }

  const handleOpenDialogOpen = () => {
    handleFileMenuClose()
    setOpenDialogOpen(true)
  }

  const handleOpenDialogClose = () => {
    setOpenDialogOpen(false)
    setOpenSearchTerm('')
  }

  const handleManageDialogOpen = () => {
    handleFileMenuClose()
    setManageDialogOpen(true)
  }

  const handleManageDialogClose = () => {
    setManageDialogOpen(false)
  }

  const requestDelete = (agent?: CustomAgent) => {
    const target = agent || selectedCustomAgent
    if (!target) return
    handleFileMenuClose()
    setPendingDeleteAgent(target)
    setDeleteConfirmOpen(true)
  }

  const handleDeleteCancel = () => {
    setDeleteConfirmOpen(false)
    setPendingDeleteAgent(null)
  }

  const handleDeleteConfirm = async () => {
    if (!pendingDeleteAgent) return
    const target = pendingDeleteAgent
    setDeleteConfirmOpen(false)
    setPendingDeleteAgent(null)
    await handleDeleteById(target)
  }

  return (
    <Box sx={{ p: 2, height: '100%', overflow: 'auto' }}>
      <Stack spacing={2}>
        <Typography variant="h6">Prompt Workshop</Typography>

        {error && <Alert severity="error">{error}</Alert>}
        {status && <Alert severity="success">{status}</Alert>}

        {selectedCustomAgent?.parent_prompt_stale && (
          <Alert
            severity="warning"
            action={
              <Button color="inherit" size="small" onClick={handleRebase} disabled={saving}>
                Rebase
              </Button>
            }
          >
            Parent prompt changed since this custom agent was created.
          </Alert>
        )}

        {selectedCustomAgent && !selectedCustomAgent.parent_exists && (
          <Alert severity="error">
            Parent agent is unavailable, so this custom agent cannot be executed.
          </Alert>
        )}

        <Toolbar>
          <MenuTrigger onClick={handleFileMenuOpen}>File</MenuTrigger>
          <StyledMenu
            anchorEl={fileMenuAnchor}
            open={Boolean(fileMenuAnchor)}
            onClose={handleFileMenuClose}
            anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
            transformOrigin={{ vertical: 'top', horizontal: 'left' }}
          >
            <StyledMenuItem
              onClick={() => {
                handleFileMenuClose()
                handleNew()
              }}
            >
              <span>New Prompt</span>
            </StyledMenuItem>
            <StyledMenuItem onClick={handleOpenDialogOpen}>
              <span>Open Prompt...</span>
            </StyledMenuItem>
            <StyledMenuItem onClick={handleManageDialogOpen}>
              <span>Manage Prompts...</span>
            </StyledMenuItem>
            <Divider />
            <StyledMenuItem onClick={handleSave} disabled={saving}>
              <span>{selectedCustomAgentId ? 'Save Prompt' : 'Save New Prompt'}</span>
            </StyledMenuItem>
            <StyledMenuItem onClick={() => requestDelete()} disabled={!selectedCustomAgentId || saving}>
              <span>Delete Prompt</span>
            </StyledMenuItem>
          </StyledMenu>

          <Box sx={{ ml: 'auto', pr: 1, display: 'flex', alignItems: 'center', gap: 1 }}>
            <Typography variant="caption" color="text.secondary">
              {selectedCustomAgent ? `Editing: ${selectedCustomAgent.name}` : 'Editing: New draft'}
            </Typography>
            {(loading || saving) && <CircularProgress size={16} />}
          </Box>
        </Toolbar>

        <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <FormControl size="small" sx={{ minWidth: 220, flex: 1 }}>
            <InputLabel>Parent Agent</InputLabel>
            <Select
              label="Parent Agent"
              value={parentAgentId}
              onChange={(event) => setParentAgentId(event.target.value)}
            >
              {parentAgents.map((agent) => (
                <MenuItem key={agent.agent_id} value={agent.agent_id}>
                  {agent.agent_name}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        </Box>

        <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
          <TextField
            size="small"
            label="Custom Agent Name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            sx={{ flex: 2, minWidth: 220 }}
          />
          <FormControl size="small" sx={{ flex: 0, minWidth: 110 }}>
            <InputLabel>Icon</InputLabel>
            <Select
              label="Icon"
              value={icon}
              onChange={(event) => setIcon(event.target.value)}
            >
              {iconOptions.map((option) => (
                <MenuItem key={option} value={option}>
                  {option}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        </Box>

        <TextField
          fullWidth
          size="small"
          label="Description"
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />

        <FormControlLabel
          control={
            <Checkbox
              checked={includeModRules}
              onChange={(event) => setIncludeModRules(event.target.checked)}
            />
          }
          label="Include MOD rules at runtime"
        />

        <TextField
          label="Prompt"
          fullWidth
          multiline
          minRows={12}
          value={customPrompt}
          onChange={(event) => setCustomPrompt(event.target.value)}
          sx={{
            '& .MuiInputBase-root': {
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
              fontSize: '0.85rem',
            },
          }}
        />

        <Paper variant="outlined" sx={{ p: 2 }}>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>
            MOD Prompt Overrides
          </Typography>
          {availableModIds.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              This parent agent has no MOD-specific prompts to override.
            </Typography>
          ) : (
            <Stack spacing={1.5}>
              <Stack direction="row" spacing={1} alignItems="center">
                <Select
                  size="small"
                  value={modId}
                  onChange={(event) => setModId(event.target.value)}
                  sx={{ minWidth: 160 }}
                >
                  {availableModIds.map((availableModId) => (
                    <MenuItem key={availableModId} value={availableModId}>
                      {availableModId}
                    </MenuItem>
                  ))}
                </Select>
                <Button
                  variant="outlined"
                  onClick={handleResetSelectedModPrompt}
                  disabled={!hasSelectedModOverride}
                >
                  Reset to Parent
                </Button>
              </Stack>
              <TextField
                fullWidth
                multiline
                minRows={8}
                label={selectedModId ? `${selectedModId} Prompt` : 'MOD Prompt'}
                value={selectedModPrompt}
                onChange={(event) => handleSelectedModPromptChange(event.target.value)}
                sx={{
                  '& .MuiInputBase-root': {
                    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
                    fontSize: '0.8rem',
                  },
                }}
              />
              <Typography variant="caption" color="text.secondary">
                {hasSelectedModOverride
                  ? `Custom override active for ${selectedModId}.`
                  : `Using parent ${selectedModId} prompt content.`}
                {hasAnyModOverrides ? ` Total overrides: ${Object.keys(modPromptOverrides).length}.` : ''}
              </Typography>
            </Stack>
          )}
        </Paper>

        <TextField
          fullWidth
          size="small"
          label="Save Notes (for version history via File > Save)"
          value={saveNotes}
          onChange={(event) => setSaveNotes(event.target.value)}
        />

        <Paper variant="outlined" sx={{ p: 2 }}>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>
            Version History
          </Typography>
          {versions.length === 0 && (
            <Typography variant="body2" color="text.secondary">
              No versions yet
            </Typography>
          )}
          <Stack spacing={1}>
            {versions.map((version) => (
              <Stack
                key={version.id}
                direction="row"
                spacing={1}
                alignItems="center"
                justifyContent="space-between"
              >
                <Typography variant="body2">
                  v{version.version} {version.notes ? `- ${version.notes}` : ''}
                </Typography>
                <Button
                  size="small"
                  variant="text"
                  onClick={() => handleRevert(version.version)}
                  disabled={!selectedCustomAgentId || saving}
                >
                  Revert
                </Button>
              </Stack>
            ))}
          </Stack>
        </Paper>

        <Dialog
          open={openDialogOpen}
          onClose={handleOpenDialogClose}
          maxWidth="sm"
          fullWidth
          PaperProps={{ sx: { borderRadius: 2, maxHeight: '70vh' } }}
        >
          <DialogTitle sx={{ pb: 1 }}>
            <Typography variant="h6" sx={{ fontSize: '1rem', fontWeight: 600 }}>
              Open Prompt
            </Typography>
          </DialogTitle>
          <DialogContent sx={{ pt: 1 }}>
            <TextField
              fullWidth
              size="small"
              placeholder="Search prompts..."
              value={openSearchTerm}
              onChange={(event) => setOpenSearchTerm(event.target.value)}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchIcon sx={{ fontSize: 18, color: 'text.secondary' }} />
                  </InputAdornment>
                ),
              }}
              sx={{ mb: 2 }}
            />
            <Box sx={{ minHeight: 200, maxHeight: 320, overflow: 'auto' }}>
              {loading ? (
                <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
                  <CircularProgress size={24} />
                </Box>
              ) : filteredOpenAgents.length === 0 ? (
                <Box sx={{ textAlign: 'center', py: 4, color: 'text.secondary' }}>
                  <Typography variant="body2">
                    {openSearchTerm ? 'No prompts match your search' : 'No saved prompts yet'}
                  </Typography>
                </Box>
              ) : (
                <List disablePadding>
                  {filteredOpenAgents.map((agent) => (
                    <ListItem key={agent.id} disablePadding>
                      <ListItemButton
                        onClick={() => {
                          setSelectedCustomAgentId(agent.id)
                          handleOpenDialogClose()
                          setStatus(`Opened "${agent.name}"`)
                        }}
                        selected={agent.id === selectedCustomAgentId}
                        sx={{
                          borderRadius: 1,
                          mb: 0.5,
                          '&.Mui-selected': {
                            backgroundColor: (theme) => alpha(theme.palette.primary.main, 0.12),
                          },
                        }}
                      >
                        <DescriptionOutlinedIcon sx={{ fontSize: 18, mr: 1.5, color: 'text.secondary' }} />
                        <ListItemText
                          primary={agent.name}
                          secondary={agent.description || 'Custom prompt'}
                          primaryTypographyProps={{ fontSize: '0.85rem' }}
                          secondaryTypographyProps={{ fontSize: '0.75rem' }}
                        />
                      </ListItemButton>
                    </ListItem>
                  ))}
                </List>
              )}
            </Box>
          </DialogContent>
          <DialogActions sx={{ px: 3, pb: 2 }}>
            <Button onClick={handleOpenDialogClose} size="small">
              Cancel
            </Button>
          </DialogActions>
        </Dialog>

        <Dialog
          open={manageDialogOpen}
          onClose={handleManageDialogClose}
          maxWidth="sm"
          fullWidth
          PaperProps={{ sx: { borderRadius: 2, maxHeight: '70vh' } }}
        >
          <DialogTitle sx={{ pb: 1 }}>
            <Typography variant="h6" sx={{ fontSize: '1rem', fontWeight: 600 }}>
              Manage Prompts
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              Open or delete your saved prompts for this parent agent
            </Typography>
          </DialogTitle>
          <DialogContent sx={{ pt: 1 }}>
            <Box sx={{ minHeight: 200, maxHeight: 360, overflow: 'auto' }}>
              {loading ? (
                <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
                  <CircularProgress size={24} />
                </Box>
              ) : customAgents.length === 0 ? (
                <Box sx={{ textAlign: 'center', py: 4, color: 'text.secondary' }}>
                  <Typography variant="body2">No saved prompts yet</Typography>
                </Box>
              ) : (
                <List disablePadding>
                  {customAgents.map((agent) => (
                    <ListItem
                      key={agent.id}
                      disablePadding
                      sx={{
                        mb: 0.5,
                        border: (theme) => `1px solid ${theme.palette.divider}`,
                        borderRadius: 1,
                        backgroundColor:
                          agent.id === selectedCustomAgentId
                            ? (theme) => alpha(theme.palette.primary.main, 0.08)
                            : 'transparent',
                      }}
                    >
                      <Box sx={{ display: 'flex', alignItems: 'center', width: '100%', py: 0.5, px: 1 }}>
                        <DescriptionOutlinedIcon sx={{ fontSize: 18, mr: 1.5, color: 'text.secondary' }} />
                        <Box sx={{ flex: 1, minWidth: 0 }}>
                          <Typography
                            variant="body2"
                            sx={{
                              fontSize: '0.85rem',
                              overflow: 'hidden',
                              textOverflow: 'ellipsis',
                              whiteSpace: 'nowrap',
                            }}
                          >
                            {agent.name}
                          </Typography>
                          <Typography variant="caption" color="text.secondary">
                            {agent.description || 'Custom prompt'}
                            {agent.id === selectedCustomAgentId && ' • Currently open'}
                          </Typography>
                        </Box>
                        <Button
                          size="small"
                          variant="text"
                          onClick={() => {
                            setSelectedCustomAgentId(agent.id)
                            setStatus(`Opened "${agent.name}"`)
                          }}
                        >
                          Open
                        </Button>
                        <Tooltip title="Delete">
                          <IconButton
                            size="small"
                            onClick={() => requestDelete(agent)}
                            sx={{ color: 'error.main' }}
                            disabled={saving}
                          >
                            <DeleteIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      </Box>
                    </ListItem>
                  ))}
                </List>
              )}
            </Box>
          </DialogContent>
          <DialogActions sx={{ px: 3, pb: 2 }}>
            <Button onClick={handleManageDialogClose} size="small">
              Close
            </Button>
          </DialogActions>
        </Dialog>

        <Dialog
          open={deleteConfirmOpen}
          onClose={handleDeleteCancel}
          PaperProps={{ sx: { minWidth: 320, borderRadius: 2 } }}
        >
          <DialogTitle sx={{ fontSize: '1rem' }}>Delete Prompt?</DialogTitle>
          <DialogContent>
            <Typography variant="body2" color="text.secondary">
              Are you sure you want to delete &ldquo;{pendingDeleteAgent?.name}&rdquo;? This action cannot be undone.
            </Typography>
          </DialogContent>
          <DialogActions sx={{ px: 3, pb: 2 }}>
            <Button onClick={handleDeleteCancel} disabled={saving} size="small">
              Cancel
            </Button>
            <Button
              onClick={handleDeleteConfirm}
              color="error"
              variant="contained"
              disabled={saving}
              size="small"
            >
              {saving ? 'Deleting...' : 'Delete'}
            </Button>
          </DialogActions>
        </Dialog>
      </Stack>
    </Box>
  )
}

export default PromptWorkshop
