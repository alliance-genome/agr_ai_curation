import { Box, Button, Stack, Typography } from '@mui/material'

import type { CustomAgentVersion } from '@/types/promptExplorer'

import { formatVersionDate } from './workshopDraftUtils'
import { DataTable, HelpText, LinkButtonSx, MONO_FONT, Section, SectionHeading } from './workshopStyles'

export interface VersionsSectionProps {
  versions: CustomAgentVersion[]
  hasAgent: boolean
  saving: boolean
  onRevert: (version: number) => void
}

export default function VersionsSection({ versions, hasAgent, saving, onRevert }: VersionsSectionProps) {
  const sorted = [...versions].sort((a, b) => b.version - a.version)
  const currentVersion = sorted[0]?.version

  return (
    <Stack spacing={2.5}>
      <Section>
        <SectionHeading>Version history</SectionHeading>
        {!hasAgent ? (
          <HelpText>Save this agent to start its version history.</HelpText>
        ) : sorted.length === 0 ? (
          <HelpText>No versions yet.</HelpText>
        ) : (
          <DataTable>
            <Box sx={{ overflowX: 'auto' }}>
              <table aria-label="Version history">
                <thead>
                  <tr>
                    <th scope="col">Version</th>
                    <th scope="col">Note</th>
                    <th scope="col">Saved</th>
                    <th scope="col"><span style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>Actions</span></th>
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((version) => {
                    const isCurrent = version.version === currentVersion
                    return (
                      <Box
                        component="tr"
                        key={version.id}
                        aria-current={isCurrent ? 'true' : undefined}
                        sx={isCurrent ? { backgroundColor: 'action.selected' } : undefined}
                      >
                        <td style={{ width: 72 }}>
                          <Typography component="span" sx={{ fontFamily: MONO_FONT, fontSize: 12 }}>
                            v{version.version}
                          </Typography>
                        </td>
                        <td>
                          {version.notes ? (
                            <Typography component="span" sx={{ fontSize: 13 }}>{version.notes}</Typography>
                          ) : (
                            <Typography component="span" sx={{ fontSize: 13, fontStyle: 'italic', color: 'text.disabled' }}>
                              No note
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
                              onClick={() => onRevert(version.version)}
                              disabled={saving}
                              aria-label={`Revert to version ${version.version}`}
                              sx={LinkButtonSx}
                            >
                              Revert
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
          <HelpText>Revert creates a new version from the old one. Nothing is deleted.</HelpText>
        )}
      </Section>
    </Stack>
  )
}
