import { Box, Button, Stack, Typography } from '@mui/material'

import type { AgentExecutionRevision } from '@/types/agentExecution'

import { formatVersionDate } from './workshopDraftUtils'
import { DataTable, HelpText, LinkButtonSx, MONO_FONT, Section, SectionHeading } from './workshopStyles'

export interface VersionsSectionProps {
  versions: AgentExecutionRevision[]
  currentRevisionId?: string | null
  hasAgent: boolean
  saving: boolean
  loading?: boolean
  error?: string | null
  hasMore?: boolean
  onLoadMore?: () => void
  onRetry?: () => void
  onRevert: (version: AgentExecutionRevision) => void
}

export default function VersionsSection({ versions, currentRevisionId, hasAgent, saving, loading, error, hasMore, onLoadMore, onRetry, onRevert }: VersionsSectionProps) {
  const sorted = [...versions].sort((a, b) => b.revision - a.revision)

  return (
    <Stack spacing={2.5}>
      <Section>
        <SectionHeading>Version history</SectionHeading>
        {error && <Typography role="alert" color="error">{error}</Typography>}
        {error && onRetry && <Button onClick={onRetry} disabled={loading || saving}>Retry loading configurations</Button>}
        {loading && <HelpText role="status">Loading saved configurations…</HelpText>}
        {!hasAgent ? (
          <HelpText>Save this agent to start its version history.</HelpText>
        ) : sorted.length === 0 ? (
          !loading && !error && <HelpText>No saved configurations yet.</HelpText>
        ) : (
          <DataTable>
            <Box sx={{ overflowX: 'auto' }}>
              <table aria-label="Version history">
                <thead>
                  <tr>
                    <th scope="col">Version</th>
                    <th scope="col">Saved configuration</th>
                    <th scope="col">Saved</th>
                    <th scope="col"><span style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>Actions</span></th>
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((version) => {
                    const isCurrent = version.id === currentRevisionId
                    return (
                      <Box
                        component="tr"
                        key={version.id}
                        aria-current={isCurrent ? 'true' : undefined}
                        sx={isCurrent ? { backgroundColor: 'action.selected' } : undefined}
                      >
                        <td style={{ width: 72 }}>
                          <Typography component="span" sx={{ fontFamily: MONO_FONT, fontSize: 12 }}>
                            v{version.revision}
                          </Typography>
                        </td>
                        <td>
                          <Typography component="span" sx={{ fontSize: 13 }}>
                            {version.snapshot.model_id} · {version.snapshot.tool_ids.length} tools
                            {' · '}{version.snapshot.output_contract.output_state === 'none'
                              ? 'No structured output'
                              : version.snapshot.output_contract.output_mode === 'domain'
                                ? version.snapshot.output_contract.output_schema_key
                                : version.snapshot.output_contract.output_mode === 'profile_bound_generic'
                                  ? `Output structure v${version.snapshot.output_contract.generic_profile_ref.revision}`
                                  : 'Generic output (no structure)'}
                          </Typography>
                          {version.notes && (
                            <Typography sx={{ fontSize: 13, color: 'text.secondary', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
                              {version.notes}
                            </Typography>
                          )}
                        </td>
                        <td style={{ width: 150, whiteSpace: 'nowrap' }}>
                          <Typography component="span" sx={{ fontFamily: MONO_FONT, fontSize: 12, color: 'text.secondary' }}>
                            {formatVersionDate(version.created_at)}
                          </Typography>
                        </td>
                        <td style={{ width: 90, textAlign: 'right' }}>
                          {isCurrent ? (
                            <Typography component="span" sx={{ fontSize: 12.5, fontWeight: 500, color: 'text.secondary' }}>
                              Current
                            </Typography>
                          ) : (
                            <Button
                              size="small"
                              onClick={() => onRevert(version)}
                              disabled={saving || !currentRevisionId}
                              aria-label={`Restore configuration ${version.revision}`}
                              sx={LinkButtonSx}
                            >
                              Restore
                            </Button>
                          )}
                        </td>
                      </Box>
                    )
                  })}
                </tbody>
              </table>
            </Box>
          </DataTable>
        )}
        {hasAgent && sorted.length > 0 && (
          <HelpText>Restore copies the complete saved configuration into a new version. Nothing is deleted.</HelpText>
        )}
        {hasMore && !error && <Button onClick={onLoadMore} disabled={loading || saving}>Load older configurations</Button>}
        {hasAgent && <HelpText>Older prompt-only history is not a complete configuration and cannot be restored.</HelpText>}
      </Section>
    </Stack>
  )
}
