/**
 * AgentPromptsTab
 *
 * One reading pane for the selected agent's prompt layers. A segmented layer
 * picker with character counts (Core, Generated, Base, Group, Override,
 * Effective), the group select when the agent has group rules, one Copy
 * button for the visible layer, a meta line, and a monospace pane capped to
 * the panel height. Effective shows every layer joined with a label at each
 * boundary and the group rules highlighted.
 */

import { useState } from 'react'
import type { ReactNode } from 'react'
import {
  Alert,
  Box,
  Button,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  ToggleButton,
  ToggleButtonGroup,
} from '@mui/material'
import { alpha } from '@mui/material/styles'
import ContentCopyIcon from '@mui/icons-material/ContentCopy'
import LockOutlinedIcon from '@mui/icons-material/LockOutlined'
import EditOutlinedIcon from '@mui/icons-material/EditOutlined'

import type { CombinedPromptResponse, PromptInfo, PromptLayerInfo } from '@/types/promptExplorer'
import { MONO_FONT_FAMILY } from './agentGuidePrimitives'

export type PromptLayerKey = 'core' | 'generated' | 'base' | 'group' | 'override' | 'effective'

interface AgentPromptsTabProps {
  agent: PromptInfo
  selectedGroupId: string | null
  onGroupSelect: (groupId: string | null) => void
  combinedPrompt: CombinedPromptResponse | null
  loadingCombined: boolean
}

interface LayerView {
  key: PromptLayerKey
  label: string
  copyLabel: string
  content: string
  emptyText: string
}

interface EffectiveSegment {
  id: string
  label: string
  content: string
  highlight: boolean
}

export function formatCharCount(count: number): string {
  if (count < 1000) return String(count)
  return `${(count / 1000).toFixed(1).replace(/\.0$/, '')}k`
}

function joinContent(layers: PromptLayerInfo[]): string {
  return layers.map((layer) => layer.content).filter(Boolean).join('\n\n')
}

const DEFAULT_OVERLAY_REVIEW_MESSAGE = 'Curator overlay needs coordinator review before it can be included in the effective prompt.'

function AgentPromptsTab({
  agent,
  selectedGroupId,
  onGroupSelect,
  combinedPrompt,
  loadingCombined,
}: AgentPromptsTabProps) {
  const [selectedLayer, setSelectedLayer] = useState<PromptLayerKey>('effective')

  const overlayNeedsReview = agent.custom_prompt_overlay_status === 'needs_review'
  const overlayReviewMessage = agent.custom_prompt_warning || DEFAULT_OVERLAY_REVIEW_MESSAGE
  const groupActive = Boolean(selectedGroupId && agent.has_group_rules)
  const manifestLayers = groupActive && combinedPrompt ? combinedPrompt.layer_manifest.layers : null

  const agentLayers = agent.prompt_layers || []
  const sourceLayers = manifestLayers ?? agentLayers
  const byKind = (kind: PromptLayerInfo['kind']) => sourceLayers.filter((layer) => layer.kind === kind)

  const selectedGroupRule = selectedGroupId ? agent.group_rules[selectedGroupId] : undefined
  const groupLayerContent = joinContent(byKind('group_rules')) || selectedGroupRule?.content || ''
  const overrideContent = overlayNeedsReview ? agent.base_prompt : joinContent(byKind('curator_overlay'))
  const baseContent = joinContent(byKind('base_prompt')) || (agentLayers.length === 0 ? agent.base_prompt : '')

  const effectiveSegments: EffectiveSegment[] = sourceLayers.length > 0
    ? sourceLayers.filter((layer) => layer.content).map((layer) => ({
      id: layer.id,
      label: [layer.title, layer.provenance].filter(Boolean).join(' · '),
      content: layer.content,
      highlight: layer.kind === 'group_rules',
    }))
    : [{ id: 'base', label: 'Base prompt', content: overlayNeedsReview ? '' : agent.base_prompt, highlight: false }]
      .filter((segment) => segment.content)
  const effectiveContent = effectiveSegments.map((segment) => segment.content).join('\n\n')
  const effectiveLoading = groupActive && loadingCombined && !combinedPrompt
  const effectiveHash = (groupActive && combinedPrompt ? combinedPrompt.effective_prompt_hash : agent.effective_prompt_hash) || ''

  const layers: LayerView[] = [
    {
      key: 'core',
      label: 'Core',
      copyLabel: 'Copy core prompt',
      content: joinContent(byKind('core_static')),
      emptyText: 'No backend-owned core prompt layer was returned for this agent.',
    },
    {
      key: 'generated',
      label: 'Generated',
      copyLabel: 'Copy generated contract',
      content: joinContent(byKind('core_generated')),
      emptyText: 'No generated runtime contract layer is required for this agent.',
    },
    {
      key: 'base',
      label: 'Base',
      copyLabel: 'Copy base prompt',
      content: baseContent,
      emptyText: 'No base prompt was returned for this agent.',
    },
    {
      key: 'group',
      label: 'Group',
      copyLabel: 'Copy group rules',
      content: groupLayerContent,
      emptyText: selectedGroupId
        ? `No group rules were returned for ${selectedGroupId}.`
        : (agent.has_group_rules ? 'Select a group to view its rules.' : 'This agent has no group rules.'),
    },
    {
      key: 'override',
      label: 'Override',
      copyLabel: 'Copy main prompt override',
      content: overrideContent,
      emptyText: 'No main prompt override is applied.',
    },
    {
      key: 'effective',
      label: 'Effective',
      copyLabel: 'Copy effective prompt',
      content: effectiveContent,
      emptyText: overlayNeedsReview && effectiveSegments.length === 0
        ? overlayReviewMessage
        : 'No effective prompt could be assembled for this agent.',
    },
  ]
  const visibleLayer = layers.find((layer) => layer.key === selectedLayer) ?? layers[layers.length - 1]

  const handleCopy = () => {
    navigator.clipboard.writeText(visibleLayer.content).catch((err) => {
      console.error('Failed to copy:', err)
    })
  }

  const paneSx = {
    border: 1,
    borderColor: 'divider',
    borderRadius: 2,
    backgroundColor: 'background.default',
    px: 1.75,
    py: 1.5,
    fontFamily: MONO_FONT_FAMILY,
    fontSize: 12.5,
    lineHeight: 1.55,
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
    color: 'text.primary',
    flex: 1,
    minHeight: 160,
    overflow: 'auto',
  } as const

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, height: '100%', minHeight: 0 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25, flexWrap: 'wrap' }}>
        <ToggleButtonGroup
          exclusive
          size="small"
          value={visibleLayer.key}
          onChange={(_event, nextValue: PromptLayerKey | null) => {
            if (nextValue) setSelectedLayer(nextValue)
          }}
          aria-label="Prompt layer"
          sx={{ flexWrap: 'wrap', '& .MuiToggleButton-root': { textTransform: 'none', fontSize: 12.5, py: 0.5, px: 1.25, gap: 0.75 } }}
        >
          {layers.map((layer) => (
            <ToggleButton key={layer.key} value={layer.key} aria-label={`${layer.label}, ${layer.content.length} characters`}>
              {layer.label}
              <Box
                component="span"
                aria-hidden
                sx={{ fontSize: 10.5, px: 0.5, borderRadius: 0.5, backgroundColor: 'action.hover', color: 'text.secondary' }}
              >
                {formatCharCount(layer.content.length)}
              </Box>
            </ToggleButton>
          ))}
        </ToggleButtonGroup>

        {agent.has_group_rules && (
          <FormControl size="small" sx={{ minWidth: 140 }}>
            <InputLabel id="agent-prompts-group-label">Group</InputLabel>
            <Select
              labelId="agent-prompts-group-label"
              value={selectedGroupId || ''}
              label="Group"
              onChange={(event) => onGroupSelect(event.target.value || null)}
              sx={{ fontSize: 13, '& .MuiSelect-select': { py: 0.625 } }}
            >
              <MenuItem value="">
                <em>None</em>
              </MenuItem>
              {Object.keys(agent.group_rules).map((groupId) => (
                <MenuItem key={groupId} value={groupId}>
                  {groupId}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        )}

        <Button
          size="small"
          variant="outlined"
          startIcon={<ContentCopyIcon />}
          onClick={handleCopy}
          disabled={!visibleLayer.content}
          sx={{ ml: 'auto', textTransform: 'none', whiteSpace: 'nowrap' }}
        >
          {visibleLayer.copyLabel}
        </Button>
      </Box>

      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.75, flexWrap: 'wrap', fontSize: 12.5, color: 'text.secondary' }}>
        <MetaMarker icon={<LockOutlinedIcon sx={{ fontSize: 13 }} />} tone="neutral">Core and Generated are read-only</MetaMarker>
        <MetaMarker icon={<EditOutlinedIcon sx={{ fontSize: 13 }} />} tone="success">Base and Group editable in Workshop</MetaMarker>
        {effectiveHash && (
          <Box component="span">
            Effective hash <Box component="span" sx={{ fontFamily: MONO_FONT_FAMILY }}>{effectiveHash.slice(0, 12)}</Box>
          </Box>
        )}
      </Box>

      {agent.prompt_layer_error && (
        <Alert severity="error" variant="outlined" sx={{ py: 0.25 }}>
          {agent.prompt_layer_error}
        </Alert>
      )}
      {overlayNeedsReview && (
        <Alert severity="warning" variant="outlined" sx={{ py: 0.25 }}>
          {overlayReviewMessage}
        </Alert>
      )}

      <Box
        role="region"
        aria-label={`${visibleLayer.label} prompt`}
        data-testid="prompt-reading-pane"
        sx={paneSx}
      >
        {visibleLayer.key === 'effective' ? (
          effectiveLoading ? (
            <Box component="span" sx={{ color: 'text.secondary', fontFamily: 'inherit' }}>Loading effective prompt…</Box>
          ) : effectiveSegments.length === 0 ? (
            <Box component="span" sx={{ color: 'text.secondary' }}>{visibleLayer.emptyText}</Box>
          ) : (
            effectiveSegments.map((segment, index) => (
              <Box key={segment.id} component="span" sx={{ display: 'block', mt: index === 0 ? 0 : 1.25 }}>
                <Box
                  component="span"
                  sx={{
                    display: 'block',
                    mb: 0.5,
                    fontFamily: (theme) => theme.typography.fontFamily,
                    fontSize: 10.5,
                    letterSpacing: '0.06em',
                    textTransform: 'uppercase',
                    color: 'text.disabled',
                  }}
                >
                  {segment.label}
                </Box>
                <Box
                  component="span"
                  data-highlight={segment.highlight ? 'group-rules' : undefined}
                  sx={segment.highlight ? {
                    backgroundColor: (theme) => alpha(theme.palette.warning.main, theme.palette.mode === 'dark' ? 0.14 : 0.12),
                    borderRadius: 0.5,
                    boxDecorationBreak: 'clone',
                  } : undefined}
                >
                  {segment.content}
                </Box>
              </Box>
            ))
          )
        ) : (
          visibleLayer.content || <Box component="span" sx={{ color: 'text.secondary' }}>{visibleLayer.emptyText}</Box>
        )}
      </Box>
    </Box>
  )
}

function MetaMarker({ icon, tone, children }: { icon: ReactNode; tone: 'neutral' | 'success'; children: ReactNode }) {
  return (
    <Box
      component="span"
      sx={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 0.625,
        px: 0.875,
        py: '1px',
        borderRadius: 0.5,
        fontSize: 11,
        fontWeight: 600,
        letterSpacing: '0.04em',
        textTransform: 'uppercase',
        color: tone === 'success' ? 'success.main' : 'text.secondary',
        backgroundColor: (theme) => (tone === 'success'
          ? alpha(theme.palette.success.main, theme.palette.mode === 'dark' ? 0.14 : 0.12)
          : theme.palette.action.hover),
      }}
    >
      {icon}
      {children}
    </Box>
  )
}

export default AgentPromptsTab
