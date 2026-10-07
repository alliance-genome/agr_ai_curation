import { Alert, AlertTitle, Box, Button, Typography } from '@mui/material'

interface StudioReminderProps {
  onDismiss: () => void
}

/** Advisory, in-flow notice: no modal, focus grab, countdown, or blocked composer. */
export default function StudioReminder({ onDismiss }: StudioReminderProps) {
  return (
    <Box sx={{ px: { xs: 1, sm: 2 }, py: 1, flexShrink: 0 }}>
      <Alert
        severity="info"
        variant="outlined"
        role="status"
        sx={{
          borderColor: 'divider',
          bgcolor: 'background.paper',
          color: 'text.primary',
          borderRadius: 2,
          '& .MuiAlert-message': { width: '100%', minWidth: 0 },
        }}
      >
        <AlertTitle sx={{ fontWeight: 600 }}>Want help adjusting your results or flow?</AlertTitle>
        <Typography variant="body2" sx={{ lineHeight: 1.5 }}>
          Agent Studio can help explain what happened, troubleshoot an extraction, or change your prompts, agents, or flows.
        </Typography>
        <Typography variant="body2" sx={{ mt: 1, lineHeight: 1.5 }}>
          Hover over an assistant response, click the <strong>three dots (⋯)</strong>, and choose{' '}
          <strong>Open in Agent Studio</strong> to bring this conversation along.
        </Typography>
        <Button onClick={onDismiss} size="small" sx={{ mt: 1, minHeight: 44, color: 'text.primary', textTransform: 'none' }}>
          Dismiss for this chat
        </Button>
      </Alert>
    </Box>
  )
}
