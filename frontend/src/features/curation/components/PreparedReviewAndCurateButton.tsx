import ReviewAndCurateButton, {
  type ReviewAndCurateButtonProps,
} from '@/features/curation/components/ReviewAndCurateButton'
import { normalizeCurationWorkspaceScopeValues } from '@/features/curation/navigation/openCurationWorkspace'

type PreparedReviewAndCurateButtonProps = Omit<ReviewAndCurateButtonProps, 'sessionId'> & {
  sessionId?: string | null
}

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
      sessionId={sessionId}
      adapterKeys={normalizeCurationWorkspaceScopeValues(adapterKeys)}
      profileKeys={normalizeCurationWorkspaceScopeValues(profileKeys)}
      domainKeys={normalizeCurationWorkspaceScopeValues(domainKeys)}
    />
  )
}
