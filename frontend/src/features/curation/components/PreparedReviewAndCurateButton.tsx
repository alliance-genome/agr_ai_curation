import ReviewAndCurateButton, {
  type ReviewAndCurateButtonProps,
} from '@/features/curation/components/ReviewAndCurateButton'
import { normalizeCurationWorkspaceScopeValues } from '@/features/curation/navigation/openCurationWorkspace'

type PreparedReviewAndCurateButtonProps = Omit<ReviewAndCurateButtonProps, 'sessionId'> & {
  sessionId?: string | null
}

/**
 * Thin wrapper around ReviewAndCurateButton that normalizes scope keys and
 * defers availability resolution to click time — no mount-time backend probes.
 */
export default function PreparedReviewAndCurateButton({
  sessionId,
  adapterKeys,
  profileKeys,
  domainKeys,
  ...buttonProps
}: PreparedReviewAndCurateButtonProps) {
  return (
    <ReviewAndCurateButton
      {...buttonProps}
      sessionId={sessionId ?? null}
      adapterKeys={normalizeCurationWorkspaceScopeValues(adapterKeys)}
      profileKeys={normalizeCurationWorkspaceScopeValues(profileKeys)}
      domainKeys={normalizeCurationWorkspaceScopeValues(domainKeys)}
    />
  )
}
