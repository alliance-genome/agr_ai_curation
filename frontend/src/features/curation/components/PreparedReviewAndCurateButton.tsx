import { useQuery } from '@tanstack/react-query'

import ReviewAndCurateButton, {
  type ReviewAndCurateButtonProps,
} from '@/features/curation/components/ReviewAndCurateButton'
import { getCurationWorkspaceLaunchAvailability } from '@/features/curation/navigation/openCurationWorkspace'

function normalizeScopeValues(values?: string[] | null): string[] {
  return [...new Set((values ?? []).map((value) => value.trim()).filter(Boolean))]
}

type PreparedReviewAndCurateButtonProps = Omit<ReviewAndCurateButtonProps, 'sessionId'> & {
  sessionId?: string | null
}

export default function PreparedReviewAndCurateButton({
  sessionId,
  documentId,
  flowRunId,
  originSessionId,
  adapterKeys,
  profileKeys,
  domainKeys,
  ...buttonProps
}: PreparedReviewAndCurateButtonProps) {
  const normalizedAdapterKeys = normalizeScopeValues(adapterKeys)
  const normalizedProfileKeys = normalizeScopeValues(profileKeys)
  const normalizedDomainKeys = normalizeScopeValues(domainKeys)

  const launchAvailabilityQuery = useQuery({
    queryKey: [
      'curation-launch-availability',
      sessionId ?? null,
      documentId ?? null,
      flowRunId ?? null,
      originSessionId ?? null,
      normalizedAdapterKeys,
      normalizedProfileKeys,
      normalizedDomainKeys,
    ],
    queryFn: () => getCurationWorkspaceLaunchAvailability({
      sessionId,
      documentId,
      flowRunId,
      originSessionId,
      adapterKeys: normalizedAdapterKeys,
      profileKeys: normalizedProfileKeys,
      domainKeys: normalizedDomainKeys,
    }),
    enabled: Boolean(!sessionId && documentId),
    staleTime: 30_000,
  })

  const resolvedSessionId = sessionId ?? launchAvailabilityQuery.data?.existingSessionId ?? null
  const canLaunch = Boolean(resolvedSessionId || launchAvailabilityQuery.data?.canBootstrap)

  if (!sessionId && (launchAvailabilityQuery.isLoading || launchAvailabilityQuery.isError || !canLaunch)) {
    return null
  }

  if (!canLaunch) {
    return null
  }

  return (
    <ReviewAndCurateButton
      {...buttonProps}
      sessionId={resolvedSessionId}
      documentId={documentId}
      flowRunId={flowRunId}
      originSessionId={originSessionId}
      adapterKeys={normalizedAdapterKeys}
      profileKeys={normalizedProfileKeys}
      domainKeys={normalizedDomainKeys}
    />
  )
}
