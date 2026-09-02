import { Box, ButtonBase, Typography } from '@mui/material'

import type { GettingStartedMode } from './workshopDraftUtils'

export interface WorkshopStartScreenProps {
  onChoose: (mode: GettingStartedMode) => void
  hasTemplates: boolean
  hasSavedAgents: boolean
}

interface Choice {
  mode: GettingStartedMode
  title: string
  description: string
  disabledReason?: string
}

export default function WorkshopStartScreen({ onChoose, hasTemplates, hasSavedAgents }: WorkshopStartScreenProps) {
  const choices: Choice[] = [
    {
      mode: 'template',
      title: 'From a template',
      description: 'Start from a package agent and adjust its prompt.',
      disabledReason: hasTemplates ? undefined : 'No templates are installed.',
    },
    {
      mode: 'scratch',
      title: 'From scratch',
      description: 'Write the prompt yourself. Built-in instructions still apply.',
    },
    {
      mode: 'clone',
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
      <Typography sx={{ fontSize: 13 }}>Pick where it comes from. You can rename it and change anything afterwards.</Typography>
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
          <ButtonBase
            key={choice.mode}
            onClick={() => onChoose(choice.mode)}
            disabled={Boolean(choice.disabledReason)}
            sx={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'flex-start',
              textAlign: 'left',
              gap: 0.25,
              p: 1.5,
              border: (theme) => `1px solid ${theme.palette.divider}`,
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
        ))}
      </Box>
    </Box>
  )
}
