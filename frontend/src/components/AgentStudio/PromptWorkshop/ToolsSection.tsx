import { Box, Button, Chip, CircularProgress, IconButton, Stack, Typography } from '@mui/material'
import { alpha } from '@mui/material/styles'
import AddIcon from '@mui/icons-material/Add'
import AutoFixHighIcon from '@mui/icons-material/AutoFixHigh'
import CloseIcon from '@mui/icons-material/Close'

import type { ToolIdeaRequest, ToolLibraryItem } from '@/types/promptExplorer'

import { shortRequestId, toolIdeaStatusColor, toolIdeaStatusLabel } from './workshopDraftUtils'
import { DataTable, HelpText, LinkButtonSx, MONO_FONT, Section, SectionHeading } from './workshopStyles'

export interface ToolsSectionProps {
  selectedToolIds: string[]
  toolLibrary: ToolLibraryItem[]
  onRemoveTool: (toolKey: string) => void
  onAddTools: () => void
  hasTemplate: boolean
  requests: ToolIdeaRequest[]
  requestsLoading: boolean
  onNewRequest: () => void
  onAskClaudeToDraft?: () => void
}

/** Policy badge for an attached tool, or null when nothing needs calling out. */
export function toolPolicyBadge(tool: ToolLibraryItem | undefined): string | null {
  if (!tool) return null
  if (!tool.allow_execute) return 'disabled by policy'
  if (tool.config.requires_document === true) return 'needs document'
  return null
}

function PolicyBadge({ label }: { label: string }) {
  return (
    <Box
      component="span"
      sx={(theme) => ({
        fontSize: 10.5,
        px: 0.625,
        ml: 0.75,
        borderRadius: 0.5,
        fontWeight: 600,
        letterSpacing: '0.04em',
        textTransform: 'uppercase',
        whiteSpace: 'nowrap',
        backgroundColor: alpha(theme.palette.warning.main, 0.12),
        color: theme.palette.mode === 'dark' ? theme.palette.warning.light : theme.palette.warning.dark,
      })}
    >
      {label}
    </Box>
  )
}

export default function ToolsSection({
  selectedToolIds,
  toolLibrary,
  onRemoveTool,
  onAddTools,
  hasTemplate,
  requests,
  requestsLoading,
  onNewRequest,
  onAskClaudeToDraft,
}: ToolsSectionProps) {
  const toolByKey = new Map(toolLibrary.map((tool) => [tool.tool_key, tool]))

  return (
    <Stack spacing={2.5}>
      <Section>
        <SectionHeading
          action={(
            <Button size="small" onClick={onAddTools} startIcon={<AddIcon sx={{ fontSize: 15 }} />} sx={LinkButtonSx}>
              Add tools
            </Button>
          )}
        >
          Attached tools
        </SectionHeading>
        {selectedToolIds.length === 0 ? (
          <HelpText>No tools attached. Add tools so the agent can look things up or read documents.</HelpText>
        ) : (
          <DataTable>
            <Box sx={{ overflowX: 'auto' }}>
              <table aria-label="Attached tools">
                <thead>
                  <tr>
                    <th scope="col">Tool</th>
                    <th scope="col">Purpose</th>
                    <th scope="col"><span style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>Actions</span></th>
                  </tr>
                </thead>
                <tbody>
                  {selectedToolIds.map((toolKey) => {
                    const tool = toolByKey.get(toolKey)
                    const badge = toolPolicyBadge(tool)
                    const muted = Boolean(tool && !tool.allow_execute)
                    return (
                      <tr key={toolKey}>
                        <td style={{ width: 220 }}>
                          <Typography
                            component="span"
                            sx={{ fontFamily: MONO_FONT, fontSize: 12.5, fontWeight: 500, color: muted ? 'text.disabled' : 'text.primary' }}
                          >
                            {toolKey}
                          </Typography>
                        </td>
                        <td>
                          <Typography component="span" sx={{ fontSize: 13, color: muted ? 'text.disabled' : 'text.secondary' }}>
                            {tool?.description || tool?.display_name || 'No description available'}
                          </Typography>
                          {badge && <PolicyBadge label={badge} />}
                        </td>
                        <td style={{ width: 40, textAlign: 'right' }}>
                          <IconButton size="small" aria-label={`Remove ${toolKey}`} onClick={() => onRemoveTool(toolKey)}>
                            <CloseIcon sx={{ fontSize: 16 }} />
                          </IconButton>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </Box>
          </DataTable>
        )}
        <HelpText>
          {hasTemplate
            ? 'Tools the template already uses are attached when you start. Removing one here changes only this agent.'
            : 'Attach only the tools this agent needs.'}
        </HelpText>
      </Section>

      <Section>
        <SectionHeading
          action={(
            <Button size="small" onClick={onNewRequest} startIcon={<AddIcon sx={{ fontSize: 15 }} />} sx={LinkButtonSx}>
              New request
            </Button>
          )}
        >
          Requests to developers
        </SectionHeading>
        {requestsLoading && requests.length === 0 ? (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 1 }}>
            <CircularProgress size={14} />
            <HelpText>Loading requests</HelpText>
          </Box>
        ) : requests.length === 0 ? (
          <HelpText>No requests sent yet.</HelpText>
        ) : (
          <Box component="ul" aria-label="Requests to developers" sx={{ listStyle: 'none', m: 0, p: 0 }}>
            {requests.map((request) => (
              <Box
                component="li"
                key={request.id}
                sx={{
                  display: 'grid',
                  gridTemplateColumns: '1fr auto',
                  gap: 1.25,
                  alignItems: 'center',
                  py: 0.875,
                  borderTop: (theme) => `1px solid ${theme.palette.divider}`,
                  '&:last-of-type': { borderBottom: (theme) => `1px solid ${theme.palette.divider}` },
                }}
              >
                <Box sx={{ minWidth: 0 }}>
                  <Typography sx={{ fontSize: 13, fontWeight: 500 }} noWrap>
                    {request.title}
                  </Typography>
                  <Typography sx={{ fontSize: 11, color: 'text.disabled' }}>
                    Sent {new Date(request.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
                    {' · '}request {shortRequestId(request.id)}
                  </Typography>
                </Box>
                <Chip
                  size="small"
                  color={toolIdeaStatusColor(request.status)}
                  variant="outlined"
                  label={toolIdeaStatusLabel(request)}
                  sx={{ height: 22, fontSize: 11 }}
                />
              </Box>
            ))}
          </Box>
        )}
        {onAskClaudeToDraft && (
          <HelpText>
            Not sure what to ask for?{' '}
            <Button
              size="small"
              onClick={onAskClaudeToDraft}
              startIcon={<AutoFixHighIcon sx={{ fontSize: 13 }} />}
              sx={{ ...LinkButtonSx, verticalAlign: 'baseline' }}
            >
              Ask Claude to draft a request
            </Button>
          </HelpText>
        )}
      </Section>
    </Stack>
  )
}
