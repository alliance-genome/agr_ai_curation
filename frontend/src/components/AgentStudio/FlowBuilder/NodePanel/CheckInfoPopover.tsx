/**
 * Info popover for one automatic check.
 *
 * Opens from the info circle beside a check's switch. Everything it says
 * comes from the validation attachment payload (domain pack YAML): what the
 * check does, the fields it checks, and what happens when it is off. The two
 * links lead to the Agent Browser, where the validator's guide and the
 * envelope field live.
 */

import { useId, useState } from 'react'
import type { MouseEvent } from 'react'
import { Box, IconButton, Link, Popover, Typography } from '@mui/material'
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined'

import { MONO_FONT_FAMILY } from '../../agentGuidePrimitives'
import { targetSentence } from './automaticChecks'
import type { CheckGroupView } from './automaticChecks'
import type { AgentBrowserRequest } from '../types'

interface CheckInfoPopoverProps {
  check: CheckGroupView
  /** Catalog id of the agent whose envelope the check targets. */
  envelopeAgentId: string
  /** Display name of the validator agent, when the catalog knows it. */
  validatorAgentName?: string
  onOpenAgent?: (request: AgentBrowserRequest) => void
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Box>
      <Typography
        component="div"
        sx={{ fontSize: 10.5, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'text.secondary', mb: 0.25 }}
      >
        {title}
      </Typography>
      {children}
    </Box>
  )
}

function CheckInfoPopover({ check, envelopeAgentId, validatorAgentName, onOpenAgent }: CheckInfoPopoverProps) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  const titleId = useId()
  const open = Boolean(anchor)

  const handleOpen = (event: MouseEvent<HTMLElement>) => setAnchor(event.currentTarget)
  const handleClose = () => setAnchor(null)

  const fieldTarget = check.targets.find((target) => target.objectType)
  const guideLink = check.validatorAgentId && validatorAgentName && onOpenAgent
    ? { agentId: check.validatorAgentId, label: `Open ${validatorAgentName}` }
    : null

  return (
    <>
      <IconButton
        size="small"
        aria-label={`About this check: ${check.curatorLabel}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={handleOpen}
        sx={{ ml: 'auto', color: open ? 'primary.main' : 'text.secondary', flex: 'none' }}
      >
        <InfoOutlinedIcon sx={{ fontSize: 18 }} />
      </IconButton>
      <Popover
        open={open}
        anchorEl={anchor}
        onClose={handleClose}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
        role="dialog"
        aria-labelledby={titleId}
        slotProps={{ paper: { sx: { width: 340, p: 1.75, display: 'flex', flexDirection: 'column', gap: 1, fontSize: 12.5, lineHeight: 1.45 } } }}
      >
        <Typography id={titleId} component="h3" sx={{ m: 0, fontSize: 13, fontWeight: 600 }}>
          {check.curatorLabel}
        </Typography>

        {check.description && (
          <Section title="What it does">
            <Typography sx={{ fontSize: 12.5 }}>{check.description}</Typography>
          </Section>
        )}

        <Section title="Checks these fields">
          <Box component="ul" sx={{ m: 0, pl: 2, display: 'flex', flexDirection: 'column', gap: 0.25 }}>
            {check.targets.map((target) => (
              <Box component="li" key={`${target.attachmentId}:${target.fieldPath ?? ''}`} sx={{ fontSize: 12.5 }}>
                {targetSentence(target)}
                {target.fieldPath && (
                  <Box
                    component="code"
                    sx={{ fontFamily: MONO_FONT_FAMILY, fontSize: 11, color: 'text.disabled', ml: 0.75 }}
                  >
                    {target.fieldPath}
                  </Box>
                )}
              </Box>
            ))}
          </Box>
        </Section>

        {check.whenOff && (
          <Section title="If turned off">
            <Typography sx={{ fontSize: 12.5 }}>{check.whenOff}</Typography>
          </Section>
        )}

        {onOpenAgent && (guideLink || fieldTarget) && (
          <Box sx={{ display: 'flex', gap: 1.5, mt: 0.25, flexWrap: 'wrap' }}>
            {guideLink && (
              <Link
                component="button"
                type="button"
                underline="hover"
                sx={{ fontSize: 12.5, fontWeight: 500 }}
                onClick={() => {
                  handleClose()
                  onOpenAgent({ agentId: guideLink.agentId, tab: 'guide' })
                }}
              >
                {guideLink.label}
              </Link>
            )}
            {fieldTarget && (
              <Link
                component="button"
                type="button"
                underline="hover"
                sx={{ fontSize: 12.5, fontWeight: 500 }}
                onClick={() => {
                  handleClose()
                  onOpenAgent({
                    agentId: envelopeAgentId,
                    tab: 'envelope',
                    focus: { objectType: fieldTarget.objectType as string, fieldPath: fieldTarget.fieldPath },
                  })
                }}
              >
                Field details
              </Link>
            )}
          </Box>
        )}
      </Popover>
    </>
  )
}

export default CheckInfoPopover
