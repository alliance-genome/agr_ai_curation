import { useState } from 'react'
import HelpOutlineIcon from '@mui/icons-material/HelpOutline'
import { Box, Button, ButtonBase, Dialog, DialogActions, DialogContent, DialogTitle, IconButton, Typography } from '@mui/material'

import type { AgentMetadata } from '../../../services/agentStudioService'
import type { GettingStartedMode } from './workshopDraftUtils'

export interface WorkshopStartScreenProps {
  onChoose: (mode: GettingStartedMode) => void
  onCustomExtraction?: () => void
  agents?: Record<string, AgentMetadata>
  hasTemplates: boolean
  hasSavedAgents: boolean
}

interface Choice {
  mode: GettingStartedMode
  title: string
  description: string
  help: string
  disabledReason?: string
}

export default function WorkshopStartScreen({ onChoose, onCustomExtraction, agents = {}, hasTemplates, hasSavedAgents }: WorkshopStartScreenProps) {
  const [help, setHelp] = useState<{ title: string; text: string } | null>(null)
  const envelopes = [...new Map(Object.values(agents).flatMap((agent) =>
    agent.domain_envelope && (agent.domain_extraction_ref || agent.output_schema_key)
      && agent.visible !== false && agent.is_active !== false
      ? [[agent.domain_envelope.domain_pack_id, { ...agent.domain_envelope, display_name: agent.name }] as const] : [],
  )).values()].sort((a, b) => a.display_name.localeCompare(b.display_name))
  const choices: Choice[] = [
    {
      mode: 'template',
      help: 'Templates provide a starting prompt, tools, and output format. Choose a specialized extraction template when you need a supported Alliance data structure. You can review and adjust its settings before saving.',
      title: 'From a template',
      description: 'Start from a package agent and adjust its prompt.',
      disabledReason: hasTemplates ? undefined : 'No templates are installed.',
    },
    {
      mode: 'scratch',
      help: 'Start with an empty prompt and choose the tools and output format yourself. Use custom data extraction for guided help designing information to collect from papers.',
      title: 'From scratch',
      description: 'Write the prompt yourself. Built-in instructions still apply.',
    },
    {
      mode: 'clone',
      help: 'Start a separate agent using one you already saved. Changes to the copy do not change the original agent or flows that use it.',
      title: 'Clone one of yours',
      description: 'Copy an agent you already saved.',
      disabledReason: hasSavedAgents ? undefined : 'You have no saved agents yet.',
    },
  ]

  return (
    <Box
      role="group"
      aria-labelledby="workshop-start-title"
      sx={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 1,
        textAlign: 'center',
        color: 'text.secondary',
        p: 3.5,
        border: (theme) => `1px dashed ${theme.palette.divider}`,
        borderRadius: 2,
        fontSize: 13,
      }}
    >
      <Typography id="workshop-start-title" component="h3" sx={{ fontSize: 14, fontWeight: 500, color: 'text.primary', m: 0 }}>
        Start a new agent
      </Typography>
      <Typography sx={{ fontSize: 13 }}>Choose a starting point. You can review and adjust your agent before saving.</Typography>
      <Box sx={{ width: '100%', maxWidth: 620, mt: 1, border: 1, borderColor: 'primary.main', borderRadius: 2, display: 'flex', alignItems: 'center', bgcolor: 'background.paper' }}>
        <ButtonBase onClick={onCustomExtraction} disabled={!onCustomExtraction}
          sx={{ flex: 1, p: 2, textAlign: 'left', display: 'block', borderRadius: 2, '&.Mui-focusVisible': { outline: '2px solid', outlineColor: 'primary.main' } }}>
          <Typography sx={{ fontWeight: 600, color: 'text.primary' }}>Custom data extraction</Typography>
          <Typography sx={{ fontSize: 13, color: 'text.secondary', mt: 0.5 }}>
            {onCustomExtraction ? 'Choose what to find in a paper and design the details to collect—such as stocks, reagents, or other custom information.' : 'A general PDF extraction template must be available to start.'}
          </Typography>
        </ButtonBase>
        <IconButton aria-label="About custom extraction" onClick={() => setHelp({ title: 'Custom data extraction', text: 'The wizard helps you name an item type and choose its details. It prepares a PDF extraction agent for you. These custom records are separate from Alliance data structures (sometimes called envelopes); they are not automatically ready for Alliance submission. For a supported Alliance structure, start from its specialized template.' })} sx={{ mr: 1 }}><HelpOutlineIcon fontSize="small" /></IconButton>
      </Box>
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: 'repeat(3, 1fr)',
          gap: 1.25,
          width: '100%',
          maxWidth: 620,
          mt: 0.75,
          '@container workshop (max-width: 719px)': { gridTemplateColumns: '1fr' },
        }}
      >
        {choices.map((choice) => (
          <Box key={choice.mode} sx={{ display: 'flex', flexDirection: 'column', border: 1, borderColor: 'divider', borderRadius: 2 }}>
          <ButtonBase
            onClick={() => onChoose(choice.mode)}
            disabled={Boolean(choice.disabledReason)}
            sx={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'flex-start',
              textAlign: 'left',
              gap: 0.25,
              p: 1.5,
              flex: 1,
              borderRadius: 2,
              backgroundColor: 'background.paper',
              '&:hover': { borderColor: 'primary.main', backgroundColor: 'action.hover' },
              '&.Mui-focusVisible': {
                outline: (theme) => `2px solid ${theme.palette.primary.main}`,
                outlineOffset: 2,
              },
              '&.Mui-disabled': { color: 'text.disabled' },
            }}
          >
            <Typography component="span" sx={{ fontSize: 13, fontWeight: 500, color: 'inherit' }}>
              {choice.title}
            </Typography>
            <Typography component="span" sx={{ fontSize: 12, color: 'inherit', opacity: 0.85 }}>
              {choice.disabledReason ?? choice.description}
            </Typography>
          </ButtonBase>
          <IconButton aria-label={`About ${choice.mode === 'template' ? 'templates' : choice.mode === 'scratch' ? 'starting from scratch' : 'cloning'}`} size="small"
            onClick={() => setHelp({ title: choice.title, text: choice.help })} sx={{ alignSelf: 'flex-end', mr: 0.5, mb: 0.5 }}><HelpOutlineIcon fontSize="small" /></IconButton>
          </Box>
        ))}
      </Box>
      <Dialog open={Boolean(help)} onClose={() => setHelp(null)} aria-labelledby="start-help-title" maxWidth="sm" fullWidth>
        <DialogTitle id="start-help-title">{help?.title}</DialogTitle>
        <DialogContent>
          <Typography>{help?.text}</Typography>
          {help?.title === 'Custom data extraction' && <Box sx={{ mt: 2 }}>
            <Typography sx={{ fontWeight: 600, mb: 1 }}>Alliance structure support in this installation</Typography>
            {envelopes.length === 0 ? <Typography>Support details are unavailable. Review the specialized template’s Output format for its current status.</Typography> : envelopes.map((envelope) => {
              const stillDeveloping = envelope.status !== 'active' || [...envelope.model_definitions, ...envelope.object_definitions, ...envelope.schema_refs].some((definition) => definition.definition_state && definition.definition_state !== 'stable')
              return <Box key={envelope.domain_pack_id} sx={{ py: 1, borderBottom: 1, borderColor: 'divider' }}>
                <Typography>{envelope.display_name}</Typography>
                <Typography variant="body2" color="text.secondary">{stillDeveloping ? 'Still being developed' : 'Standard structure available'}</Typography>
              </Box>
            })}
            <Typography variant="body2" sx={{ mt: 1.5 }}>Available structures still need validation and curator review. Custom extraction can collect other information while specialized structures are being developed.</Typography>
          </Box>}
        </DialogContent>
        <DialogActions><Button onClick={() => setHelp(null)}>Close</Button></DialogActions>
      </Dialog>
    </Box>
  )
}
