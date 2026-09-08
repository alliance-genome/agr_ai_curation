import { useEffect, useRef, useState } from 'react'
import {
  Box,
  Button,
  Checkbox,
  Chip,
  Collapse,
  FormControl,
  FormHelperText,
  InputLabel,
  ListItemText,
  MenuItem,
  Select,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material'
import ChevronRightIcon from '@mui/icons-material/ChevronRight'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import LockOutlinedIcon from '@mui/icons-material/LockOutlined'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'

import type { AgentTemplate, CustomAgent, GroupOption, ModelOption } from '@/types/promptExplorer'

import {
  ALL_GROUPS_VALUE,
  formatReasoningLabel,
  normalizeReasoningValue,
  type GettingStartedMode,
  type WorkshopVisibility,
} from './workshopDraftUtils'
import { FieldRow, HelpText, InfoNote, LinkButtonSx, Section, SectionHeading } from './workshopStyles'

export interface EnvelopeSummary {
  /** Domain pack status, for the state dot. */
  status: string
  /** "Validation findings on Disease annotation objects" or "Disease annotation objects". */
  producesLabel: string
  activeChecks: number
  underDevelopment: number
}

export interface SetupSectionProps {
  gettingStartedMode: GettingStartedMode
  onModeChange: (mode: GettingStartedMode) => void
  templateOptions: AgentTemplate[]
  parentAgentId: string
  onTemplateChange: (agentId: string) => void
  /** Set when the open agent names a template that is not installed. */
  missingTemplateId: string | null
  templateAllowedGroupIds: string[]
  customAgents: CustomAgent[]
  cloneSourceAgentId: string
  onCloneSourceChange: (agentId: string) => void
  isExistingAgent: boolean
  /** Increment to move focus to the origin selector (used after the start screen). */
  focusOriginToken: number

  icon: string
  iconOptions: string[]
  onIconChange: (icon: string) => void
  name: string
  onNameChange: (name: string) => void
  description: string
  onDescriptionChange: (description: string) => void

  envelope: EnvelopeSummary | null
  onViewEnvelope?: () => void

  modelOptions: ModelOption[]
  selectedModelId: string
  onModelChange: (modelId: string) => void
  selectedModelOption: ModelOption | null
  selectedModelReasoning: string
  onReasoningChange: (reasoning: string) => void
  reasoningDescription: string
  onAskClaudeAboutModels?: () => void

  visibility: WorkshopVisibility
  onVisibilityChange: (visibility: WorkshopVisibility) => void
  allowedGroupIds: string[]
  onAllowedGroupIdsChange: (groupIds: string[]) => void
  selectableGroupOptions: GroupOption[]
  inheritedAllowedGroupIds: string[]
}

function reasoningHelperLine(model: ModelOption | null, selected: string, description: string): string {
  if (!model || !model.supports_reasoning || model.reasoning_options.length === 0 || !selected) {
    return ''
  }
  const label = formatReasoningLabel(selected)
  const modelDefault = normalizeReasoningValue(model.default_reasoning)
  const detail = description ? ` ${description}.` : ''
  if (modelDefault && modelDefault === normalizeReasoningValue(selected)) {
    return `${label} is the default reasoning for ${model.name}.${detail}`
  }
  if (modelDefault) {
    return `${label} reasoning selected. The default for ${model.name} is ${formatReasoningLabel(modelDefault)}.${detail}`
  }
  return `${label} reasoning selected.${detail}`
}

export default function SetupSection(props: SetupSectionProps) {
  const {
    gettingStartedMode,
    onModeChange,
    templateOptions,
    parentAgentId,
    onTemplateChange,
    missingTemplateId,
    templateAllowedGroupIds,
    customAgents,
    cloneSourceAgentId,
    onCloneSourceChange,
    isExistingAgent,
    focusOriginToken,
    icon,
    iconOptions,
    onIconChange,
    name,
    onNameChange,
    description,
    onDescriptionChange,
    envelope,
    onViewEnvelope,
    modelOptions,
    selectedModelId,
    onModelChange,
    selectedModelOption,
    selectedModelReasoning,
    onReasoningChange,
    reasoningDescription,
    onAskClaudeAboutModels,
    visibility,
    onVisibilityChange,
    allowedGroupIds,
    onAllowedGroupIdsChange,
    selectableGroupOptions,
    inheritedAllowedGroupIds,
  } = props

  const [guidanceOpen, setGuidanceOpen] = useState(false)
  const originSelectRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (focusOriginToken === 0) return
    const control = originSelectRef.current?.querySelector<HTMLElement>('[role="combobox"]')
    control?.focus()
  }, [focusOriginToken])

  const supportsReasoning = Boolean(
    selectedModelOption?.supports_reasoning && (selectedModelOption?.reasoning_options.length ?? 0) > 0
  )
  const helperLine = reasoningHelperLine(selectedModelOption, selectedModelReasoning, reasoningDescription)
  const hasGuidance = Boolean(selectedModelOption)
  const inheritedFloor = inheritedAllowedGroupIds.length > 0

  return (
    <Stack spacing={2.5}>
      <Section>
        <SectionHeading>Starting point</SectionHeading>
        {missingTemplateId && (
          <InfoNote data-tone="warning" role="alert">
            <LockOutlinedIcon sx={{ fontSize: 16, mt: 0.25 }} />
            <span>
              <strong style={{ fontWeight: 500 }}>The template this agent was built from is no longer installed.</strong>
              {' '}Built-in and template layers cannot be shown. You can still run, edit, and save this agent.
            </span>
          </InfoNote>
        )}
        <FieldRow>
          <ToggleButtonGroup
            exclusive
            size="small"
            value={gettingStartedMode}
            aria-label="Starting point"
            onChange={(_event, value: GettingStartedMode | null) => {
              if (value !== null) onModeChange(value)
            }}
            sx={{ '& .MuiToggleButton-root': { textTransform: 'none', px: 1.5, py: 0.5 } }}
          >
            <ToggleButton value="template">Template</ToggleButton>
            <ToggleButton value="scratch">Scratch</ToggleButton>
            <ToggleButton value="clone">Clone</ToggleButton>
          </ToggleButtonGroup>

          {gettingStartedMode === 'template' && (
            <FormControl size="small" sx={{ width: 300 }} ref={originSelectRef}>
              <InputLabel id="workshop-template-label">Template</InputLabel>
              <Select
                labelId="workshop-template-label"
                label="Template"
                value={parentAgentId}
                disabled={templateOptions.length === 0 && !missingTemplateId}
                onChange={(event) => onTemplateChange(event.target.value)}
              >
                {templateOptions.length === 0 && !missingTemplateId && (
                  <MenuItem value="" disabled>
                    No templates available
                  </MenuItem>
                )}
                {missingTemplateId && (
                  <MenuItem value={missingTemplateId} disabled>
                    {missingTemplateId} (no longer available)
                  </MenuItem>
                )}
                {templateOptions.map((template) => (
                  <MenuItem key={template.agent_id} value={template.agent_id}>
                    {template.name}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          )}

          {gettingStartedMode === 'clone' && (
            <FormControl size="small" sx={{ width: 300 }} ref={originSelectRef}>
              <InputLabel id="workshop-clone-source-label">Clone source</InputLabel>
              <Select
                labelId="workshop-clone-source-label"
                label="Clone source"
                value={cloneSourceAgentId}
                onChange={(event) => onCloneSourceChange(event.target.value)}
              >
                {customAgents.map((agent) => (
                  <MenuItem key={agent.id} value={agent.id}>
                    {agent.name}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          )}
        </FieldRow>
        {gettingStartedMode === 'template' && !isExistingAgent && templateAllowedGroupIds.length > 0 && (
          <InfoNote>
            <LockOutlinedIcon sx={{ fontSize: 16, mt: 0.25 }} />
            <span>
              This template is restricted to {templateAllowedGroupIds.join(', ')}. Your copy can keep or narrow that,
              not widen it.
            </span>
          </InfoNote>
        )}
      </Section>

      <Section>
        <SectionHeading>Identity</SectionHeading>
        <FieldRow>
          <FormControl size="small" sx={{ width: 72, flexShrink: 0 }}>
            <InputLabel id="workshop-icon-label">Icon</InputLabel>
            <Select
              labelId="workshop-icon-label"
              label="Icon"
              value={icon}
              onChange={(event) => onIconChange(event.target.value)}
              sx={{ '& .MuiSelect-select': { textAlign: 'center', fontSize: '1.1rem' } }}
            >
              {iconOptions.map((option) => (
                <MenuItem key={option} value={option}>
                  {option}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          <TextField
            size="small"
            label="Agent name"
            value={name}
            onChange={(event) => onNameChange(event.target.value)}
            sx={{ flex: 1, minWidth: 280 }}
          />
        </FieldRow>
        <TextField
          fullWidth
          multiline
          minRows={2}
          maxRows={5}
          size="small"
          label="Description"
          value={description}
          onChange={(event) => onDescriptionChange(event.target.value)}
          placeholder="What this agent does, in one or two sentences"
        />
      </Section>

      {envelope && (
        <Section>
          <SectionHeading>What it produces</SectionHeading>
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 1.25,
              px: 1.5,
              py: 1,
              border: (theme) => `1px solid ${theme.palette.divider}`,
              borderRadius: 1.5,
              backgroundColor: 'background.default',
              fontSize: 13,
              flexWrap: 'wrap',
            }}
          >
            <Box
              aria-hidden
              sx={{
                width: 8,
                height: 8,
                borderRadius: '50%',
                flexShrink: 0,
                backgroundColor: envelope.status === 'active' ? 'success.main' : 'warning.main',
              }}
            />
            <Typography component="span" sx={{ fontSize: 13 }}>
              {envelope.producesLabel} · {envelope.activeChecks} automatic {envelope.activeChecks === 1 ? 'check' : 'checks'}
              {envelope.underDevelopment > 0 ? `, ${envelope.underDevelopment} under development` : ''}
            </Typography>
            {onViewEnvelope && (
              <Button
                size="small"
                onClick={onViewEnvelope}
                endIcon={<OpenInNewIcon sx={{ fontSize: 14 }} />}
                sx={{ ...LinkButtonSx, ml: 'auto' }}
              >
                View envelope
              </Button>
            )}
          </Box>
        </Section>
      )}

      <Section>
        <SectionHeading
          action={hasGuidance ? (
            <Button
              size="small"
              onClick={() => setGuidanceOpen((open) => !open)}
              aria-expanded={guidanceOpen}
              aria-controls="workshop-model-guidance"
              startIcon={guidanceOpen ? <ExpandMoreIcon sx={{ fontSize: 16 }} /> : <ChevronRightIcon sx={{ fontSize: 16 }} />}
              sx={LinkButtonSx}
            >
              Model guidance
            </Button>
          ) : undefined}
        >
          Model
        </SectionHeading>
        {hasGuidance && selectedModelOption && (
          <Collapse in={guidanceOpen} unmountOnExit>
            <Box
              id="workshop-model-guidance"
              sx={{
                border: (theme) => `1px solid ${theme.palette.divider}`,
                borderRadius: 1.5,
                px: 1.5,
                py: 1.25,
                fontSize: 12.5,
                color: 'text.secondary',
                display: 'flex',
                flexDirection: 'column',
                gap: 0.75,
              }}
            >
              <Typography component="div" sx={{ fontSize: 13, color: 'text.primary', fontWeight: 500 }}>
                {selectedModelOption.name}
                <Typography component="span" sx={{ fontSize: 12, color: 'text.secondary', ml: 1 }}>
                  {selectedModelOption.provider.toUpperCase()} · {selectedModelOption.model_id}
                </Typography>
              </Typography>
              {selectedModelOption.description && <span>{selectedModelOption.description}</span>}
              {selectedModelOption.guidance && selectedModelOption.guidance !== selectedModelOption.description && (
                <span>{selectedModelOption.guidance}</span>
              )}
              {selectedModelOption.recommended_for.length > 0 && (
                <Box sx={{ display: 'flex', gap: 0.75, alignItems: 'center', flexWrap: 'wrap' }}>
                  <span>Recommended for</span>
                  {selectedModelOption.recommended_for.map((item) => (
                    <Chip key={item} size="small" variant="outlined" label={item} />
                  ))}
                </Box>
              )}
              {selectedModelOption.avoid_for.length > 0 && (
                <Box sx={{ display: 'flex', gap: 0.75, alignItems: 'center', flexWrap: 'wrap' }}>
                  <span>Avoid for</span>
                  {selectedModelOption.avoid_for.map((item) => (
                    <Chip key={item} size="small" variant="outlined" label={item} />
                  ))}
                </Box>
              )}
            </Box>
          </Collapse>
        )}
        <FieldRow>
          <FormControl size="small" sx={{ width: 300 }}>
            <InputLabel id="workshop-model-label">Model</InputLabel>
            <Select
              labelId="workshop-model-label"
              label="Model"
              value={selectedModelId}
              onChange={(event) => onModelChange(event.target.value)}
            >
              {modelOptions.map((model) => (
                <MenuItem key={model.model_id} value={model.model_id}>
                  {model.name}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          {supportsReasoning && selectedModelOption && (
            <FormControl size="small" sx={{ width: 200 }}>
              <InputLabel id="workshop-reasoning-label">Reasoning</InputLabel>
              <Select
                labelId="workshop-reasoning-label"
                label="Reasoning"
                value={selectedModelReasoning}
                onChange={(event) => onReasoningChange(event.target.value)}
              >
                {selectedModelOption.reasoning_options.map((option) => (
                  <MenuItem key={option} value={option}>
                    {formatReasoningLabel(option)}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          )}
        </FieldRow>
        {(helperLine || onAskClaudeAboutModels) && (
          <HelpText>
            {helperLine}
            {onAskClaudeAboutModels && (
              <>
                {helperLine ? ' ' : ''}
                <Button size="small" onClick={onAskClaudeAboutModels} sx={{ ...LinkButtonSx, verticalAlign: 'baseline' }}>
                  Ask Claude which model fits
                </Button>
              </>
            )}
          </HelpText>
        )}
      </Section>

      <Section>
        <SectionHeading>Sharing</SectionHeading>
        <FieldRow>
          <FormControl size="small" sx={{ width: 220 }}>
            <InputLabel id="workshop-visibility-label">Visibility</InputLabel>
            <Select
              labelId="workshop-visibility-label"
              label="Visibility"
              value={visibility}
              onChange={(event) => onVisibilityChange(event.target.value as WorkshopVisibility)}
            >
              <MenuItem value="private">Private</MenuItem>
              <MenuItem value="project">Shared with project</MenuItem>
            </Select>
          </FormControl>
          <FormControl size="small" sx={{ width: 300 }}>
            {/* The select shows "All groups" for an empty value (displayEmpty + renderValue),
                so the label must stay shrunk or it overlaps that text. */}
            <InputLabel id="available-groups-label" shrink>Available to groups</InputLabel>
            <Select
              labelId="available-groups-label"
              label="Available to groups"
              multiple
              displayEmpty
              value={allowedGroupIds}
              aria-describedby={inheritedFloor ? 'available-groups-floor' : undefined}
              onChange={(event) => {
                const value = event.target.value as string[]
                const nextValue = value.includes(ALL_GROUPS_VALUE) ? [] : value
                if (inheritedFloor && nextValue.length === 0) return
                onAllowedGroupIdsChange(nextValue)
              }}
              renderValue={(selected) => {
                const groupIds = selected as string[]
                return groupIds.length === 0 ? 'All groups' : groupIds.join(', ')
              }}
            >
              {!inheritedFloor && (
                <MenuItem value={ALL_GROUPS_VALUE}>
                  <Checkbox checked={allowedGroupIds.length === 0} />
                  <ListItemText primary="All groups" />
                </MenuItem>
              )}
              {selectableGroupOptions.map((group) => (
                <MenuItem key={group.group_id} value={group.group_id}>
                  <Checkbox checked={allowedGroupIds.includes(group.group_id)} />
                  <ListItemText primary={group.name} secondary={group.group_id} />
                </MenuItem>
              ))}
            </Select>
            {inheritedFloor && (
              <FormHelperText
                id="available-groups-floor"
                sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mx: 0, fontSize: 11 }}
              >
                <LockOutlinedIcon sx={{ fontSize: 13 }} />
                <span>Inherits a {inheritedAllowedGroupIds.join(', ')} access floor; you can narrow it, not widen it.</span>
              </FormHelperText>
            )}
          </FormControl>
        </FieldRow>
        <HelpText>Sharing sets who can see this agent. Groups restrict who can run it.</HelpText>
      </Section>
    </Stack>
  )
}
