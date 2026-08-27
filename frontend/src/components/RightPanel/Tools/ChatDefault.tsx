import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import HelpOutlineIcon from '@mui/icons-material/HelpOutline'
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'
import {
  ChatRoutePreferenceApiError,
  clearChatRoutePreference,
  fetchChatRoutePreference,
  fetchChatRouteTargets,
  saveChatRoutePreference,
  type ChatRouteMode,
  type ChatRoutePreference,
  type ChatRoutePreferenceUpdate,
  type ChatRouteTarget,
} from '@/services/chatRoutePreferenceService'

const modeLabel: Record<ChatRouteMode, string> = {
  automatic: 'Automatic',
  agent: 'Agent',
  flow: 'Flow',
}

function preferenceSummary(preference: ChatRoutePreference): string {
  if (preference.mode === 'automatic') return 'Automatic routing'
  const label = preference.target?.display_name ?? 'Unavailable selection'
  return `${modeLabel[preference.mode]} · ${label}`
}

function updateForTarget(target: ChatRouteTarget): ChatRoutePreferenceUpdate {
  return target.kind === 'agent'
    ? { mode: 'agent', agent_id: target.id, flow_id: null }
    : { mode: 'flow', agent_id: null, flow_id: target.id }
}

const ChatDefault: React.FC = () => {
  const theme = useTheme()
  const [confirmed, setConfirmed] = useState<ChatRoutePreference | null>(null)
  const [draftMode, setDraftMode] = useState<ChatRouteMode>('automatic')
  const [targets, setTargets] = useState<ChatRouteTarget[] | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isPickerLoading, setIsPickerLoading] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [pickerError, setPickerError] = useState<string | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [failedUpdate, setFailedUpdate] = useState<ChatRoutePreferenceUpdate | 'automatic' | null>(null)
  const [savedMessage, setSavedMessage] = useState('')
  const [helpOpen, setHelpOpen] = useState(false)
  const [pickerQuery, setPickerQuery] = useState('')
  const savingRef = useRef(false)

  const loadPreference = useCallback(async () => {
    setIsLoading(true)
    setLoadError(null)
    try {
      const result = await fetchChatRoutePreference()
      setConfirmed(result)
      setDraftMode(result.mode)
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : 'Could not load your chat default.')
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadPreference()
  }, [loadPreference])

  const loadTargets = useCallback(async () => {
    setIsPickerLoading(true)
    setPickerError(null)
    try {
      setTargets(await fetchChatRouteTargets())
    } catch (error) {
      setPickerError(error instanceof Error ? error.message : 'Could not load chat default choices.')
    } finally {
      setIsPickerLoading(false)
    }
  }, [])

  useEffect(() => {
    if (draftMode !== 'automatic' && targets === null && !isPickerLoading && !pickerError) {
      void loadTargets()
    }
  }, [draftMode, isPickerLoading, loadTargets, pickerError, targets])

  const save = useCallback(async (update: ChatRoutePreferenceUpdate | 'automatic') => {
    if (savingRef.current) return
    savingRef.current = true
    setIsSaving(true)
    setSaveError(null)
    setSavedMessage('')
    try {
      const result = update === 'automatic'
        ? await clearChatRoutePreference()
        : await saveChatRoutePreference(update)
      setConfirmed(result)
      setDraftMode(result.mode)
      setPickerQuery('')
      setFailedUpdate(null)
      setSavedMessage(`Saved: ${preferenceSummary(result)}`)
    } catch (error) {
      const rejectedTarget = update !== 'automatic'
        && error instanceof ChatRoutePreferenceApiError
        && error.status === 404
      if (rejectedTarget) {
        const rejectedId = update.mode === 'agent' ? update.agent_id : update.flow_id
        setTargets((current) => current?.filter(
          (target) => target.kind !== update.mode || target.id !== rejectedId,
        ) ?? null)
        setFailedUpdate(null)
        setSaveError('That selection is no longer available. Your previous selection is still active. Choose another.')
      } else {
        setFailedUpdate(update)
        setSaveError("We couldn't save your chat default. Your previous selection is still active.")
      }
      setDraftMode(confirmed?.mode ?? 'automatic')
    } finally {
      savingRef.current = false
      setIsSaving(false)
    }
  }, [confirmed])

  const visibleTargets = useMemo(
    () => (targets ?? []).filter((target) => target.kind === draftMode && target.available),
    [draftMode, targets],
  )

  const handleModeChange = (_event: React.MouseEvent<HTMLElement>, nextMode: ChatRouteMode | null) => {
    if (nextMode === null || isSaving) return
    setSaveError(null)
    setFailedUpdate(null)
    setSavedMessage('')
    setPickerQuery('')
    if (nextMode === 'automatic') {
      void save('automatic')
      return
    }
    setDraftMode(nextMode)
  }

  if (isLoading) {
    return (
      <Box aria-label="Loading chat default" sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
        <CircularProgress size={24} />
      </Box>
    )
  }

  if (loadError || !confirmed) {
    return (
      <Alert
        severity="error"
        action={<Button color="inherit" onClick={() => void loadPreference()} sx={{ minHeight: 44 }}>Retry</Button>}
      >
        {loadError ?? 'Could not load your chat default.'}
      </Alert>
    )
  }

  const unavailable = confirmed.mode !== 'automatic' && confirmed.status === 'unavailable'

  return (
    <Box
      component="section"
      aria-labelledby="chat-default-title"
      sx={{
        border: `1px solid ${theme.palette.divider}`,
        borderRadius: 1,
        p: 2,
        minWidth: 0,
        backgroundColor: alpha(theme.palette.background.paper, 0.52),
      }}
    >
      <Stack spacing={1.5}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <Box sx={{ minWidth: 0, flex: 1 }}>
            <Typography id="chat-default-title" variant="subtitle1" fontWeight={600}>
              Chat default
            </Typography>
            <Typography variant="body2" color="text.secondary" noWrap title={preferenceSummary(confirmed)}>
              Current: {preferenceSummary(confirmed)}
            </Typography>
          </Box>
          <IconButton
            aria-label="About chat default"
            onClick={() => setHelpOpen(true)}
            sx={{ minWidth: 44, minHeight: 44 }}
          >
            <HelpOutlineIcon />
          </IconButton>
        </Box>

        {unavailable && (
          <Alert severity="warning">
            This saved {confirmed.mode} is no longer available. Choose another {confirmed.mode} or use Automatic routing.
          </Alert>
        )}

        <Box>
          <Typography id="chat-default-mode-label" variant="caption" component="div" sx={{ mb: 0.5 }}>
            Use for future chat requests
          </Typography>
          <ToggleButtonGroup
            exclusive
            fullWidth
            size="small"
            value={draftMode}
            onChange={handleModeChange}
            aria-labelledby="chat-default-mode-label"
            disabled={isSaving}
            sx={{
              '& .MuiToggleButton-root': { minHeight: 44, px: 0.75, textTransform: 'none' },
            }}
          >
            <ToggleButton value="automatic">Automatic</ToggleButton>
            <ToggleButton value="agent">Agent</ToggleButton>
            <ToggleButton value="flow">Flow</ToggleButton>
          </ToggleButtonGroup>
        </Box>

        {draftMode !== 'automatic' && (
          <Box>
            <Typography component="label" htmlFor="chat-default-picker" variant="caption" sx={{ display: 'block', mb: 0.5 }}>
              Choose {draftMode}
            </Typography>
            <Autocomplete
              id="chat-default-picker"
              options={visibleTargets}
              loading={isPickerLoading}
              disabled={isSaving || Boolean(pickerError)}
              value={null}
              inputValue={pickerQuery}
              onInputChange={(_event, value) => setPickerQuery(value)}
              onChange={(_event, target) => {
                if (target) void save(updateForTarget(target))
              }}
              getOptionLabel={(option) => option.display_name}
              isOptionEqualToValue={(option, value) => option.id === value.id && option.kind === value.kind}
              filterOptions={(options, state) => {
                const query = state.inputValue.trim().toLocaleLowerCase()
                if (!query) return options
                return options.filter((target) =>
                  [target.display_name, target.description, target.category]
                    .filter(Boolean)
                    .some((value) => value!.toLocaleLowerCase().includes(query)),
                )
              }}
              noOptionsText={
                visibleTargets.length === 0
                  ? draftMode === 'flow'
                    ? 'No flows are available to you yet. Create a flow in Agent Studio.'
                    : 'No agents are available to you.'
                  : `No ${draftMode}s match “${pickerQuery}”. Try a name or category.`
              }
              renderOption={(props, option) => (
                <li {...props} key={`${option.kind}-${option.id}`}>
                  <Box sx={{ minWidth: 0 }}>
                    <Typography variant="body2">{option.display_name}</Typography>
                    {(option.category || option.description) && (
                      <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                        {[modeLabel[option.kind], option.category, option.description].filter(Boolean).join(' · ')}
                      </Typography>
                    )}
                  </Box>
                </li>
              )}
              renderInput={(params) => (
                <TextField
                  {...params}
                  placeholder={`Search ${draftMode}s`}
                  inputProps={{ ...params.inputProps, 'aria-label': `Choose ${draftMode}` }}
                  InputProps={{
                    ...params.InputProps,
                    endAdornment: (
                      <>
                        {isPickerLoading ? <CircularProgress color="inherit" size={18} /> : null}
                        {params.InputProps.endAdornment}
                      </>
                    ),
                  }}
                />
              )}
            />
          </Box>
        )}

        {pickerError && (
          <Alert severity="error" action={<Button color="inherit" onClick={() => void loadTargets()} sx={{ minHeight: 44 }}>Retry</Button>}>
            {pickerError}
          </Alert>
        )}
        {saveError && (
          <Alert
            severity="error"
            role="alert"
            action={failedUpdate && (
              <Button color="inherit" onClick={() => void save(failedUpdate)} sx={{ minHeight: 44 }}>
                Retry
              </Button>
            )}
          >
            {saveError}
          </Alert>
        )}
        <Typography aria-live="polite" variant="caption" color="text.secondary" sx={{ minHeight: '1.25em' }}>
          {isSaving ? 'Saving chat default…' : savedMessage}
        </Typography>
      </Stack>

      <Dialog open={helpOpen} onClose={() => setHelpOpen(false)} aria-labelledby="chat-default-help-title">
        <DialogTitle id="chat-default-help-title">About chat default</DialogTitle>
        <DialogContent>
          <Typography>
            Your typed chat message remains the request. This setting only chooses who handles future chat requests.
            You can change or clear it at any time. Selecting an agent or flow does not grant access.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setHelpOpen(false)} sx={{ minHeight: 44 }}>Close</Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}

export default ChatDefault
