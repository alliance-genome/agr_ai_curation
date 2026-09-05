import { Stack, Typography } from '@mui/material'
import type { AgentExecutionRevision } from '@/types/agentExecution'

export default function SavedExecutionSummary({ revision }: { revision: AgentExecutionRevision }) {
  const { snapshot } = revision
  const output = snapshot.output_contract
  const pin = output.output_state === 'structured_extraction' && output.output_mode === 'profile_bound_generic' ? output.generic_profile_ref : null
  const label = output.output_state === 'none' ? 'No structured output'
    : output.output_mode === 'domain' ? `Packaged domain: ${output.domain_extraction_ref?.domain_pack_id ?? output.output_schema_key}`
      : pin ? `Custom Output Structure revision ${pin.revision}` : 'Flexible generic extraction (no profile)'
  return <details>
    <summary>Saved configuration · revision {revision.revision}</summary>
    <Stack spacing={1} sx={{ mt: 1 }}>
      <Typography color="text.secondary">This is the saved executable configuration, not the current unsaved draft.</Typography>
      <Typography>Model: {snapshot.model_id}; reasoning: {snapshot.model_reasoning || 'not configured'}; temperature: {snapshot.model_temperature}</Typography>
      <Typography sx={{ overflowWrap: 'anywhere' }}>Tools: {snapshot.tool_ids.length ? snapshot.tool_ids.join(', ') : 'none'}</Typography>
      {snapshot.system_managed_tool_ids.length > 0 && <Typography sx={{ overflowWrap: 'anywhere' }}>Platform-managed tools: {snapshot.system_managed_tool_ids.join(', ')}</Typography>}
      <Typography>{label}</Typography>
      <Typography>Access groups: {snapshot.allowed_group_ids.length ? snapshot.allowed_group_ids.join(', ') : 'no additional group restriction'}</Typography>
      <details><summary>Technical revision identifiers</summary>
        <Typography sx={{ overflowWrap: 'anywhere' }}>Agent revision: {revision.id}; fingerprint: {revision.fingerprint}</Typography>
        {pin && <Typography sx={{ overflowWrap: 'anywhere' }}>Profile: {pin.profile_id}; revision: {pin.profile_revision_id}; fingerprint: {pin.fingerprint}</Typography>}
      </details>
    </Stack>
  </details>
}
