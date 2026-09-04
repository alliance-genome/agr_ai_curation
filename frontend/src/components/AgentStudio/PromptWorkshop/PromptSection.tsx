import { useState } from 'react'
import {
  Alert,
  Box,
  Button,
  FormControlLabel,
  Stack,
  Switch,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material'
import { alpha } from '@mui/material/styles'
import type { SxProps, Theme } from '@mui/material/styles'
import AutoFixHighIcon from '@mui/icons-material/AutoFixHigh'
import EditOutlinedIcon from '@mui/icons-material/EditOutlined'
import LockOutlinedIcon from '@mui/icons-material/LockOutlined'

import type { CustomAgent } from '@/types/promptExplorer'

import { formatCharCount } from './workshopDraftUtils'
import {
  EditorFrame,
  EditorHeader,
  HelpText,
  LinkButtonSx,
  MONO_FONT,
  ReadOnlyBody,
  Section,
  SectionHeading,
} from './workshopStyles'

export type PromptLayerKey = 'core' | 'generated' | 'template' | 'yours'

export interface PromptSectionProps {
  parentCorePrompt: string
  parentGeneratedContract: string
  parentBasePrompt: string
  hasTemplate: boolean
  templateName: string
  customPrompt: string
  onCustomPromptChange: (value: string) => void
  onResetToTemplate: () => void
  overlayStatus: CustomAgent['custom_prompt_overlay_status']
  overlayWarning: string

  availableGroupIds: string[]
  selectedGroupId: string
  onGroupChange: (groupId: string) => void
  groupPromptOverrides: Record<string, string>
  selectedGroupPrompt: string
  hasSelectedGroupOverride: boolean
  onGroupPromptChange: (value: string) => void
  onResetGroupPrompt: () => void
  includeGroupRules: boolean
  onIncludeGroupRulesChange: (value: boolean) => void
  loggedInAsLabel: string
  loggedInGroupIds: string[]

  onDiscussPromptWithClaude?: () => void
}

const editorInputSx: SxProps<Theme> = {
  '& .MuiInputBase-root': {
    fontFamily: MONO_FONT,
    fontSize: 12.5,
    lineHeight: 1.55,
    borderRadius: 0,
    backgroundColor: 'background.paper',
    alignItems: 'flex-start',
  },
  '& .MuiOutlinedInput-notchedOutline': { border: 0 },
  '& .Mui-focused .MuiOutlinedInput-notchedOutline': {
    border: (theme) => `2px solid ${theme.palette.primary.main}`,
  },
}

interface LayerDefinition {
  key: PromptLayerKey
  label: string
  content: string
  locked: boolean
  readOnlyNote: string
  emptyNote: string
}

export default function PromptSection(props: PromptSectionProps) {
  const {
    parentCorePrompt,
    parentGeneratedContract,
    parentBasePrompt,
    hasTemplate,
    templateName,
    customPrompt,
    onCustomPromptChange,
    onResetToTemplate,
    overlayStatus,
    overlayWarning,
    availableGroupIds,
    selectedGroupId,
    onGroupChange,
    groupPromptOverrides,
    selectedGroupPrompt,
    hasSelectedGroupOverride,
    onGroupPromptChange,
    onResetGroupPrompt,
    includeGroupRules,
    onIncludeGroupRulesChange,
    loggedInAsLabel,
    loggedInGroupIds,
    onDiscussPromptWithClaude,
  } = props

  const [layer, setLayer] = useState<PromptLayerKey>('yours')

  const layers: LayerDefinition[] = [
    {
      key: 'core',
      label: 'Built-in',
      content: parentCorePrompt,
      locked: true,
      readOnlyNote: 'Read-only. Built-in instructions come with the package.',
      emptyNote: hasTemplate
        ? 'No built-in instruction layer was returned for this template.'
        : 'Built-in instructions are added at runtime; none are shown for an agent without a template.',
    },
    {
      key: 'generated',
      label: 'Output structure',
      content: parentGeneratedContract,
      locked: true,
      readOnlyNote: 'Read-only. Output structure is generated from the envelope.',
      emptyNote: 'No generated output-structure layer is required for this template.',
    },
    {
      key: 'template',
      label: 'Template',
      content: parentBasePrompt,
      locked: true,
      readOnlyNote: hasTemplate
        ? `Read-only. Template instructions come from ${templateName || 'the template'}.`
        : 'Read-only. This agent does not start from a template.',
      emptyNote: hasTemplate ? 'No template prompt is available.' : 'No template selected.',
    },
    {
      key: 'yours',
      label: 'Your prompt',
      content: customPrompt,
      locked: false,
      readOnlyNote: '',
      emptyNote: '',
    },
  ]
  const activeLayer = layers.find((entry) => entry.key === layer) ?? layers[3]
  const overrideGroups = Object.keys(groupPromptOverrides)
  const canResetToTemplate = hasTemplate && customPrompt !== parentBasePrompt

  return (
    <Stack spacing={2.5}>
      <Section>
        <SectionHeading
          action={onDiscussPromptWithClaude ? (
            <Button
              size="small"
              onClick={onDiscussPromptWithClaude}
              startIcon={<AutoFixHighIcon sx={{ fontSize: 14 }} />}
              sx={LinkButtonSx}
            >
              Discuss prompt changes with AI Chat
            </Button>
          ) : undefined}
        >
          Prompt layers
        </SectionHeading>

        <ToggleButtonGroup
          exclusive
          value={layer}
          aria-label="Prompt layer"
          onChange={(_event, value: PromptLayerKey | null) => {
            if (value !== null) setLayer(value)
          }}
          sx={{
            width: '100%',
            '& .MuiToggleButton-root': {
              flex: 1,
              textTransform: 'none',
              fontSize: 12.5,
              px: 1.25,
              py: 0.75,
              justifyContent: 'flex-start',
              gap: 0.75,
              minWidth: 0,
            },
          }}
        >
          {layers.map((entry) => (
            <ToggleButton
              key={entry.key}
              value={entry.key}
              aria-label={`${entry.label}${entry.locked ? ', read-only' : ''}, ${entry.content.length} characters`}
            >
              {entry.locked && <LockOutlinedIcon sx={{ fontSize: 14 }} aria-hidden />}
              <Box component="span" sx={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {entry.label}
              </Box>
              <Box
                component="span"
                aria-hidden
                sx={{
                  ml: 'auto',
                  fontSize: 10.5,
                  px: 0.5,
                  borderRadius: 0.5,
                  backgroundColor: 'action.hover',
                  color: 'text.secondary',
                  fontVariantNumeric: 'tabular-nums',
                }}
              >
                {formatCharCount(entry.content)}
              </Box>
            </ToggleButton>
          ))}
        </ToggleButtonGroup>

        {activeLayer.key === 'yours' && overlayStatus === 'needs_review' && (
          <Alert severity="warning">
            {overlayWarning || 'This saved main prompt contains locked/core prompt markers that need coordinator review before the final prompt is trusted.'}
          </Alert>
        )}

        <EditorFrame>
          <EditorHeader>
            {activeLayer.locked ? (
              <>
                <LockOutlinedIcon sx={{ fontSize: 14 }} aria-hidden />
                <span>{activeLayer.readOnlyNote}</span>
              </>
            ) : (
              <>
                <EditOutlinedIcon sx={{ fontSize: 14 }} aria-hidden />
                <span>
                  Editing your main prompt{hasTemplate ? ' · replaces the template prompt' : ''}
                </span>
                {hasTemplate && (
                  <Button
                    size="small"
                    onClick={onResetToTemplate}
                    disabled={!canResetToTemplate}
                    sx={{ ...LinkButtonSx, ml: 'auto' }}
                  >
                    Reset to template
                  </Button>
                )}
              </>
            )}
          </EditorHeader>
          {activeLayer.locked ? (
            <ReadOnlyBody role="region" aria-label={`${activeLayer.label} layer, read-only`}>
              {activeLayer.content || activeLayer.emptyNote}
            </ReadOnlyBody>
          ) : (
            <TextField
              fullWidth
              multiline
              minRows={14}
              value={customPrompt}
              onChange={(event) => onCustomPromptChange(event.target.value)}
              placeholder="Edit the main prompt for this custom agent."
              inputProps={{ 'aria-label': 'Your prompt' }}
              sx={editorInputSx}
            />
          )}
        </EditorFrame>
      </Section>

      <Section>
        <SectionHeading>Group-specific instructions</SectionHeading>
        {availableGroupIds.length === 0 ? (
          <HelpText>This template has no group-specific instructions to override.</HelpText>
        ) : (
          <>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
              <ToggleButtonGroup
                exclusive
                size="small"
                value={selectedGroupId}
                aria-label="Group"
                onChange={(_event, value: string | null) => {
                  if (value !== null) onGroupChange(value)
                }}
                sx={{ '& .MuiToggleButton-root': { textTransform: 'none', px: 1.5, py: 0.5, gap: 0.5 } }}
              >
                {availableGroupIds.map((groupId) => {
                  const edited = overrideGroups.includes(groupId)
                  return (
                    <ToggleButton key={groupId} value={groupId} aria-label={edited ? `${groupId}, edited` : groupId}>
                      {groupId}
                      {edited && (
                        <Box
                          component="span"
                          aria-hidden
                          sx={(theme) => ({
                            fontSize: 10,
                            px: 0.5,
                            borderRadius: 0.5,
                            backgroundColor: alpha(theme.palette.warning.main, 0.12),
                            color: theme.palette.mode === 'dark' ? theme.palette.warning.light : theme.palette.warning.dark,
                          })}
                        >
                          edited
                        </Box>
                      )}
                    </ToggleButton>
                  )
                })}
              </ToggleButtonGroup>
              <FormControlLabel
                sx={{ ml: 0, mr: 0 }}
                control={(
                  <Switch
                    size="small"
                    checked={includeGroupRules}
                    onChange={(event) => onIncludeGroupRulesChange(event.target.checked)}
                  />
                )}
                label={<Typography sx={{ fontSize: 13 }}>Add group instructions at runtime</Typography>}
              />
            </Box>

            {selectedGroupId ? (
              <EditorFrame>
                <EditorHeader>
                  <EditOutlinedIcon sx={{ fontSize: 14 }} aria-hidden />
                  <span>
                    {selectedGroupId} instructions · {hasSelectedGroupOverride ? 'your override' : 'template text'}
                  </span>
                  <Button
                    size="small"
                    onClick={onResetGroupPrompt}
                    disabled={!hasSelectedGroupOverride}
                    sx={{ ...LinkButtonSx, ml: 'auto' }}
                  >
                    Reset to template
                  </Button>
                </EditorHeader>
                <TextField
                  fullWidth
                  multiline
                  minRows={6}
                  value={selectedGroupPrompt}
                  onChange={(event) => onGroupPromptChange(event.target.value)}
                  inputProps={{ 'aria-label': `${selectedGroupId} instructions` }}
                  sx={editorInputSx}
                />
              </EditorFrame>
            ) : (
              <HelpText>Select a group to see or edit its instructions.</HelpText>
            )}

            <HelpText>
              You are logged in as {loggedInAsLabel}
              {loggedInGroupIds.length > 0 ? ` (${loggedInGroupIds.join(', ')})` : ''}.
              {' '}
              {overrideGroups.length > 0
                ? `Overrides: ${overrideGroups.join(', ')}. Other groups use the template text.`
                : 'All groups use the template text.'}
            </HelpText>
          </>
        )}
      </Section>
    </Stack>
  )
}
