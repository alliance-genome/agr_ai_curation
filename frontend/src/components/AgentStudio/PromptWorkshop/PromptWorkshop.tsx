import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Checkbox,
  CircularProgress,
  FormControlLabel,
  Grid,
  MenuItem,
  Paper,
  Select,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
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

interface PromptWorkshopProps {
  catalog: PromptCatalog
  initialParentAgentId?: string | null
  onContextChange?: (context: PromptWorkshopContext) => void
}

function PromptWorkshop({ catalog, initialParentAgentId, onContextChange }: PromptWorkshopProps) {
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
          setSelectedCustomAgentId(response.custom_agents[0].id)
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
    loadCustomAgents()
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
    loadVersions()
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

  const handleDelete = async () => {
    if (!selectedCustomAgentId) return
    setSaving(true)
    setError(null)
    try {
      await deleteCustomAgent(selectedCustomAgentId)
      await reloadAfterSave()
      handleNew()
      setStatus('Custom agent deleted')
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

        <Grid container spacing={2}>
          <Grid item xs={12} md={6}>
            <Typography variant="caption" color="text.secondary">
              Parent Agent
            </Typography>
            <Select
              fullWidth
              size="small"
              value={parentAgentId}
              onChange={(event) => setParentAgentId(event.target.value)}
            >
              {parentAgents.map((agent) => (
                <MenuItem key={agent.agent_id} value={agent.agent_id}>
                  {agent.agent_name}
                </MenuItem>
              ))}
            </Select>
          </Grid>
          <Grid item xs={12} md={6}>
            <Typography variant="caption" color="text.secondary">
              Custom Agent
            </Typography>
            <Select
              fullWidth
              size="small"
              value={selectedCustomAgentId}
              onChange={(event) => setSelectedCustomAgentId(event.target.value)}
              displayEmpty
            >
              <MenuItem value="">
                <em>New draft</em>
              </MenuItem>
              {customAgents.map((agent) => (
                <MenuItem key={agent.id} value={agent.id}>
                  {agent.name}
                </MenuItem>
              ))}
            </Select>
          </Grid>
        </Grid>

        <Stack direction="row" spacing={1}>
          <Button variant="outlined" onClick={handleNew}>
            New
          </Button>
          <Button
            variant="outlined"
            color="error"
            onClick={handleDelete}
            disabled={!selectedCustomAgentId || saving}
          >
            Delete
          </Button>
          {(loading || saving) && <CircularProgress size={20} />}
        </Stack>

        <Grid container spacing={2}>
          <Grid item xs={12} md={8}>
            <TextField
              fullWidth
              size="small"
              label="Custom Agent Name"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </Grid>
          <Grid item xs={12} md={4}>
            <TextField
              fullWidth
              size="small"
              label="Icon"
              value={icon}
              onChange={(event) => setIcon(event.target.value)}
              inputProps={{ maxLength: 10 }}
            />
          </Grid>
          <Grid item xs={12}>
            <TextField
              fullWidth
              size="small"
              label="Description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </Grid>
        </Grid>

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
          label="Save Notes (for version history)"
          value={saveNotes}
          onChange={(event) => setSaveNotes(event.target.value)}
        />

        <Stack direction="row" spacing={1}>
          <Button variant="contained" onClick={handleSave} disabled={saving}>
            Save
          </Button>
          {saving && <CircularProgress size={20} />}
        </Stack>

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
      </Stack>
    </Box>
  )
}

export default PromptWorkshop
