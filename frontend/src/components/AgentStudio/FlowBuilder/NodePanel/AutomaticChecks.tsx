/**
 * "Automatic checks" section of the node panel.
 *
 * A summary sentence, a helper line, then a disclosure that opens one switch
 * per check the curator may turn off. Blocking and locked checks are counted
 * in the sentences and never listed: the curator has no control over them,
 * so a row would only be noise. Every sentence on a switch and in its popover
 * comes from the domain pack YAML through the attachment payload.
 */

import { useState } from 'react'
import { Box, Button, FormControlLabel, Switch, Typography } from '@mui/material'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import ExpandLessIcon from '@mui/icons-material/ExpandLess'

import type { AgentMetadata } from '@/services/agentStudioService'
import { SectionHeading, StateDot } from '../../agentGuidePrimitives'
import type { AgentBrowserRequest } from '../types'
import CheckInfoPopover from './CheckInfoPopover'
import {
  checksHelperSentence,
  checksSummarySentence,
  customValidatorSentences,
} from './automaticChecks'
import type { AutomaticChecksView } from './automaticChecks'

interface AutomaticChecksProps {
  view: AutomaticChecksView
  envelopeAgentId: string
  agentMetadata: Record<string, AgentMetadata>
  onToggle: (attachmentIds: string[], enabled: boolean) => void
  onOpenAgent?: (request: AgentBrowserRequest) => void
}

function AutomaticChecks({ view, envelopeAgentId, agentMetadata, onToggle, onOpenAgent }: AutomaticChecksProps) {
  const [open, setOpen] = useState(false)
  const helper = checksHelperSentence(view)
  const extra = customValidatorSentences(view)
  const optionalCount = view.optional.length

  return (
    <Box component="section" sx={{ display: 'flex', flexDirection: 'column', gap: 0.75 }}>
      <SectionHeading>Automatic checks</SectionHeading>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, fontSize: 13 }}>
        <StateDot tone={view.total > 0 ? 'active' : 'none'} />
        <Typography sx={{ fontSize: 13 }}>{checksSummarySentence(view)}</Typography>
      </Box>
      {helper && (
        <Typography sx={{ fontSize: 12, color: 'text.secondary' }}>{helper}</Typography>
      )}
      {extra.map((sentence) => (
        <Typography key={sentence} sx={{ fontSize: 12, color: 'text.secondary' }}>{sentence}</Typography>
      ))}

      {optionalCount > 0 && (
        <>
          {open && (
            <Box
              role="group"
              aria-label="Optional checks"
              sx={{ display: 'flex', flexDirection: 'column', border: 1, borderColor: 'divider', borderRadius: 2, overflow: 'hidden' }}
            >
              {view.optional.map((check) => {
                const validatorAgentName = check.validatorAgentId
                  ? agentMetadata[check.validatorAgentId]?.name
                  : undefined
                return (
                  <Box
                    key={check.key}
                    sx={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 0.5,
                      px: 1.25,
                      py: 0.5,
                      '& + &': { borderTop: 1, borderColor: 'divider' },
                    }}
                  >
                    <FormControlLabel
                      sx={{ m: 0, flex: 1, minWidth: 0, gap: 0.75, '& .MuiFormControlLabel-label': { fontSize: 12.5, minWidth: 0 } }}
                      control={(
                        <Switch
                          size="small"
                          checked={check.enabled}
                          onChange={(event) => onToggle(check.attachmentIds, event.target.checked)}
                          inputProps={{ role: 'switch' }}
                        />
                      )}
                      label={(
                        <Box component="span" sx={{ color: check.enabled ? 'text.primary' : 'text.disabled' }}>
                          {check.curatorLabel}
                        </Box>
                      )}
                    />
                    <CheckInfoPopover
                      check={check}
                      envelopeAgentId={envelopeAgentId}
                      validatorAgentName={validatorAgentName}
                      onOpenAgent={onOpenAgent}
                    />
                  </Box>
                )
              })}
            </Box>
          )}
          <Button
            size="small"
            onClick={() => setOpen((current) => !current)}
            aria-expanded={open}
            startIcon={open ? <ExpandLessIcon /> : <ExpandMoreIcon />}
            sx={{ alignSelf: 'flex-start', textTransform: 'none', fontSize: 12.5, px: 0.5 }}
          >
            {open ? 'Hide optional checks' : `Adjust optional checks (${optionalCount})`}
          </Button>
        </>
      )}
    </Box>
  )
}

export default AutomaticChecks
