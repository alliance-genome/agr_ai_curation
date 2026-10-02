import { useEffect, useState } from 'react'

import { Box, LinearProgress, Typography } from '@mui/material'

import { describeFlowStep, formatElapsed, type FlowRunProgress } from './flowRunProgress'

interface FlowRunProgressBarProps {
  progress: FlowRunProgress
}

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!active) return undefined
    setNow(Date.now())
    const interval = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(interval)
  }, [active])

  return now
}

/**
 * Step progress for a flow run: which step is running, its position and the
 * elapsed time while running; where the run stopped once it fails or is
 * stopped. A completed run renders nothing so the normal result stands alone.
 */
export default function FlowRunProgressBar({ progress }: FlowRunProgressBarProps) {
  const isRunning = progress.status === 'running'
  const now = useNow(isRunning)

  if (progress.status === 'completed') return null

  const stepText = describeFlowStep(progress)
  const finishedSteps = progress.currentStep ? progress.currentStep.step - 1 : 0
  const value = Math.min(100, (finishedSteps / progress.totalSteps) * 100)
  const flowLabel = progress.flowName ? `"${progress.flowName}"` : 'The flow'

  let headline: string
  let detail: string
  if (isRunning) {
    headline = progress.flowName ? `Running ${flowLabel}` : 'Running the flow'
    detail = stepText
  } else if (progress.currentStep) {
    const position = `step ${progress.currentStep.step} of ${progress.totalSteps}: ${progress.currentStep.name}`
    headline = progress.status === 'stopped' ? `${flowLabel} was stopped` : `${flowLabel} did not finish`
    detail = `Stopped at ${position}`
  } else {
    headline = progress.status === 'stopped' ? `${flowLabel} was stopped` : `${flowLabel} did not finish`
    detail = 'Stopped before its first step started'
  }

  return (
    <Box
      data-testid="flow-run-progress"
      data-status={progress.status}
      sx={(theme) => ({
        alignSelf: 'stretch',
        flexShrink: 0,
        display: 'flex',
        flexDirection: 'column',
        gap: 0.75,
        px: 2,
        py: 1.5,
        borderRadius: 1,
        border: `1px solid ${isRunning ? theme.palette.divider : theme.palette.error.main}`,
        backgroundColor: theme.palette.background.paper,
      })}
    >
      <Typography variant="body2" sx={{ fontWeight: 600 }}>
        {headline}
      </Typography>
      <LinearProgress
        variant="determinate"
        value={value}
        color={isRunning ? 'primary' : 'error'}
        aria-label="Flow progress"
        aria-valuetext={`${finishedSteps} of ${progress.totalSteps} steps finished`}
        sx={{ height: 6, borderRadius: 3 }}
      />
      <Box sx={{ display: 'flex', justifyContent: 'space-between', gap: 2, flexWrap: 'wrap' }}>
        <Typography
          variant="body2"
          color={isRunning ? 'text.secondary' : 'error'}
          aria-live="polite"
          aria-atomic="true"
        >
          {detail}
        </Typography>
        {isRunning && (
          <Typography variant="body2" color="text.secondary">
            {`Elapsed: ${formatElapsed(now - progress.startedAtMs)}`}
          </Typography>
        )}
      </Box>
    </Box>
  )
}
