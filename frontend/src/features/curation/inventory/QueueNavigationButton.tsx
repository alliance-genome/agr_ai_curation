import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import NavigateNextRoundedIcon from '@mui/icons-material/NavigateNextRounded'
import { Alert, Button, CircularProgress, Stack } from '@mui/material'

import {
  buildCurationQueueNavigationStateFromContext,
  type CurationQueueNavigationRequest,
  useCurationNextSessionMutation,
} from '../services/curationQueueNavigationService'

interface QueueNavigationButtonProps {
  request: CurationQueueNavigationRequest
}

export default function QueueNavigationButton({
  request,
}: QueueNavigationButtonProps) {
  const navigate = useNavigate()
  const nextSessionMutation = useCurationNextSessionMutation()
  const [statusMessage, setStatusMessage] = useState<string | null>(null)
  const [isQueueExhausted, setIsQueueExhausted] = useState(false)
  const requestKey = useMemo(() => JSON.stringify(request), [request])

  useEffect(() => {
    setStatusMessage(null)
    setIsQueueExhausted(false)
  }, [requestKey])

  async function handleNavigateToNextSession() {
    setStatusMessage(null)

    try {
      const response = await nextSessionMutation.mutateAsync({
        ...request,
        direction: 'next',
      })

      if (!response.session) {
        setIsQueueExhausted(true)
        setStatusMessage('No more sessions match the current queue.')
        return
      }

      navigate(`/curation/${response.session.session_id}`, {
        state: buildCurationQueueNavigationStateFromContext(response.queue_context),
      })
    } catch (error) {
      setStatusMessage(
        error instanceof Error ? error.message : 'Unable to load the next session.',
      )
    }
  }

  const isLoading = nextSessionMutation.isPending

  return (
    <Stack spacing={1} alignItems={{ xs: 'stretch', sm: 'flex-end' }}>
      <Button
        disabled={isLoading || isQueueExhausted}
        onClick={() => {
          void handleNavigateToNextSession()
        }}
        startIcon={isLoading ? <CircularProgress color="inherit" size={16} /> : <NavigateNextRoundedIcon />}
        variant="contained"
      >
        Next unreviewed
      </Button>
      {statusMessage ? (
        <Alert severity={isQueueExhausted ? 'info' : 'error'} sx={{ py: 0 }}>
          {statusMessage}
        </Alert>
      ) : null}
    </Stack>
  )
}
