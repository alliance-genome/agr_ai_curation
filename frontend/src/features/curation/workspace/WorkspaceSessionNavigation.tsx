import { useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import NavigateBeforeRoundedIcon from '@mui/icons-material/NavigateBeforeRounded'
import NavigateNextRoundedIcon from '@mui/icons-material/NavigateNextRounded'
import { Button, CircularProgress, Stack, Typography } from '@mui/material'

import type {
  CurationNextSessionResponse,
  CurationQueueContext,
} from '../types'
import {
  buildCurationQueueNavigationStateFromContext,
  type CurationQueueNavigationRequest,
  useCurationNextSessionQuery,
} from '../services/curationQueueNavigationService'

interface WorkspaceSessionNavigationProps {
  currentSessionId: string
  queueRequest?: CurationQueueNavigationRequest | null
  queueContext?: CurationQueueContext | null
}

interface QueuePositionSummary {
  position: number | null
  totalSessions: number | null
}

function deriveQueuePositionSummary(
  queueContext: CurationQueueContext | null | undefined,
  previousResponse: CurationNextSessionResponse | undefined,
  nextResponse: CurationNextSessionResponse | undefined,
): QueuePositionSummary {
  if (typeof queueContext?.position === 'number') {
    return {
      position: queueContext.position,
      totalSessions: queueContext.total_sessions ?? null,
    }
  }

  if (typeof nextResponse?.queue_context.position === 'number') {
    return {
      position: nextResponse.session
        ? nextResponse.queue_context.position - 1
        : nextResponse.queue_context.position,
      totalSessions: nextResponse.queue_context.total_sessions ?? null,
    }
  }

  if (typeof previousResponse?.queue_context.position === 'number') {
    return {
      position: previousResponse.session
        ? previousResponse.queue_context.position + 1
        : previousResponse.queue_context.position,
      totalSessions: previousResponse.queue_context.total_sessions ?? null,
    }
  }

  return {
    position: null,
    totalSessions: null,
  }
}

function resolveQueueStatusMessage(
  queueRequest: CurationQueueNavigationRequest | null | undefined,
  isLoading: boolean,
  errorMessage: string | null,
  queuePositionSummary: QueuePositionSummary,
): string {
  if (!queueRequest) {
    return 'Queue navigation is available when you open a session from the inventory queue.'
  }

  if (errorMessage) {
    return errorMessage
  }

  if (isLoading) {
    return 'Loading queue navigation...'
  }

  if (
    typeof queuePositionSummary.position === 'number' &&
    typeof queuePositionSummary.totalSessions === 'number'
  ) {
    return `Queue ${queuePositionSummary.position} of ${queuePositionSummary.totalSessions}`
  }

  return 'Use these controls to move through the current filtered queue.'
}

export default function WorkspaceSessionNavigation({
  currentSessionId,
  queueRequest,
  queueContext,
}: WorkspaceSessionNavigationProps) {
  const navigate = useNavigate()
  const previousQuery = useCurationNextSessionQuery(
    {
      ...(queueRequest ?? {}),
      current_session_id: currentSessionId,
      direction: 'previous',
    },
    {
      enabled: Boolean(queueRequest),
    },
  )
  const nextQuery = useCurationNextSessionQuery(
    {
      ...(queueRequest ?? {}),
      current_session_id: currentSessionId,
      direction: 'next',
    },
    {
      enabled: Boolean(queueRequest),
    },
  )

  const errorMessage = previousQuery.error instanceof Error
    ? previousQuery.error.message
    : nextQuery.error instanceof Error
      ? nextQuery.error.message
      : null
  const isLoading = previousQuery.isLoading || nextQuery.isLoading
  const queuePositionSummary = useMemo(
    () => deriveQueuePositionSummary(queueContext, previousQuery.data, nextQuery.data),
    [nextQuery.data, previousQuery.data, queueContext],
  )
  const statusMessage = resolveQueueStatusMessage(
    queueRequest,
    isLoading,
    errorMessage,
    queuePositionSummary,
  )

  function handleQueueNavigation(response: CurationNextSessionResponse | undefined) {
    if (!response?.session) {
      return
    }

    navigate(`/curation/${response.session.session_id}`, {
      state: buildCurationQueueNavigationStateFromContext(response.queue_context),
    })
  }

  const previousButtonDisabled = !queueRequest || isLoading || !previousQuery.data?.session
  const nextButtonDisabled = !queueRequest || isLoading || !nextQuery.data?.session

  return (
    <Stack spacing={0.75} alignItems={{ xs: 'stretch', md: 'flex-end' }}>
      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
        <Button
          disabled={previousButtonDisabled}
          onClick={() => handleQueueNavigation(previousQuery.data)}
          startIcon={<NavigateBeforeRoundedIcon />}
          variant="outlined"
        >
          Previous session
        </Button>
        <Button
          disabled={nextButtonDisabled}
          endIcon={<NavigateNextRoundedIcon />}
          onClick={() => handleQueueNavigation(nextQuery.data)}
          variant="outlined"
        >
          Next session
        </Button>
      </Stack>
      <Stack direction="row" spacing={1} alignItems="center" justifyContent={{ xs: 'flex-start', md: 'flex-end' }}>
        {isLoading ? <CircularProgress size={14} /> : null}
        <Typography
          color={errorMessage ? 'error.main' : 'text.secondary'}
          variant="caption"
        >
          {statusMessage}
        </Typography>
      </Stack>
    </Stack>
  )
}
