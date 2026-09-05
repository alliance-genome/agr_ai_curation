import { useEffect, useRef, useState } from 'react'
import { Alert, Autocomplete, Box, Button, MenuItem, Stack, TextField, Typography } from '@mui/material'
import {
  getProfileMappingOptions, type GenericProfileContract, type ProfileMappingDiagnostic,
  type ProfileMappingOptions, type ProfileValidatorMapping, type ProfileValidatorOptions,
} from '@/services/genericProfileService'
import { canonicalAuthoringJson } from '../authoringContext'
import { friendlyValidatorName, mappingUsesField } from './profileMappingUi'
import ProfileConstantInput from './ProfileConstantInput'

interface Props {
  value: GenericProfileContract
  onChange: (value: GenericProfileContract) => void
  onValidate: () => void
  issues: ProfileMappingDiagnostic[]
  disabled?: boolean
  fieldPath?: string
  fieldName?: string
}

const POLICY_LABELS = { informational: 'Informational finding', requires_curator_review: 'Requires curator review', error: 'Validation error' }
const capabilityKey = (cap: { capability_ref: ProfileValidatorMapping['capability_ref'] }) => canonicalAuthoringJson(cap.capability_ref)

function constantSummary(value: unknown): string {
  if (value === undefined) return 'not set'
  if (value === null) return 'explicit unknown (null)'
  if (Array.isArray(value)) return value.length ? value.map((item, i) => `item ${i + 1}: ${constantSummary(item)}`).join('; ') : 'empty list'
  if (typeof value === 'object') return Object.entries(value).map(([key, item]) => `${key}: ${constantSummary(item)}`).join('; ') || 'empty group'
  return String(value)
}

/** Scientific values belong to the parent draft; only catalog/selection is local. */
export default function ProfileValidatorEditor({ value, onChange, onValidate, issues, disabled = false, fieldPath, fieldName }: Props) {
  const [options, setOptions] = useState<{ key: string; data: ProfileMappingOptions } | null>(null)
  const [selectedPath, setSelectedPath] = useState(fieldPath ?? '')
  const [selectedCapability, setSelectedCapability] = useState<string | null>(null)
  const [selectedSlot, setSelectedSlot] = useState('')
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const requestId = useRef(0)
  useEffect(() => () => { requestId.current += 1 }, [])
  const key = canonicalAuthoringJson(value.fields)
  const current = options?.key === key
  const data = current ? options.data : null
  const mappings = value.validator_mappings ?? []
  const load = async (after?: string) => {
    const request = ++requestId.current
    setLoading(true)
    setError(null)
    try {
      // Incomplete mappings must not prevent looking up valid field choices.
      const result = await getProfileMappingOptions(value, after)
      if (request !== requestId.current) return
      setOptions((previous) => ({ key, data: { ...result, capabilities: after && previous?.key === key
        ? [...new Map([...previous.data.capabilities, ...result.capabilities].map((cap) => [capabilityKey(cap), cap])).values()]
        : result.capabilities } }))
    } catch (error) {
      if (request === requestId.current) setError(error instanceof Error ? error.message : 'Could not load compatible validators.')
    } finally {
      if (request === requestId.current) setLoading(false)
    }
  }
  const patch = (index: number, mapping: ProfileValidatorMapping) => onChange({ ...value,
    validator_mappings: mappings.map((old, i) => i === index ? mapping : old) })
  const add = (cap: ProfileValidatorOptions, slot: string) => {
    const ids = new Set(mappings.map((mapping) => mapping.mapping_id))
    let index = mappings.length + 1
    while (ids.has(`validator_${index}`)) index += 1
    const reuse = cap.metadata.custom_profile_reuse
    const inputPath = cap.input_paths[slot]?.includes(selectedPath) ? selectedPath : `${selectedPath}[]`
    const field = data?.fields.find((field) => field.path === inputPath)
    if (!current || !field || !cap.selectable) return
    onChange({ ...value, validator_mappings: [...mappings, {
      mapping_id: `validator_${index}`, capability_ref: cap.capability_ref, capability_fingerprint: cap.fingerprint,
      inputs: { ...Object.fromEntries(Object.entries(reuse.inputs).filter(([, input]) => input.context_selector && input.required).map(([name]) => [name, { source: 'context' as const }])), [slot]: { source: 'field', field_path: inputPath } }, outputs: {},
      mode: field.array_domains.length ? 'per_element' : 'whole',
      policy: { unresolved: reuse.policy.unresolved_default, blocks_readiness: reuse.policy.readiness_default },
    }] })
    if (fieldPath && Object.entries(reuse.inputs).some(([name, input]) => input.required && name !== slot && !input.context_selector)) setExpanded(old => ({ ...old, [`validator_${index}`]: true }))
  }
  const fieldCapabilities = data?.capabilities.filter(cap => Object.values(cap.input_paths).some(paths => paths.includes(selectedPath) || paths.includes(`${selectedPath}[]`))) ?? []
  fieldCapabilities.sort((a,b) => (a.metadata.origin === 'custom_agent' ? 1 : 0) - (b.metadata.origin === 'custom_agent' ? 1 : 0))
  const chosen = fieldCapabilities.find(cap => capabilityKey(cap) === selectedCapability)
  return <Stack component="section" aria-label="Optional semantic validators" spacing={1.5}>
    <Typography variant="h6" component="h3">{fieldPath ? 'Validation' : 'Optional validators'}</Typography>
    <Typography>Required details and answer formats are always validated. Optionally attach a validator for the information this detail represents. An attachment does not mean an answer has passed validation.</Typography>
    {mappings.length === 0 && <Alert severity="info">No validators attached. Structural validation remains enforced.</Alert>}
    <Button disabled={disabled || loading} onClick={() => void load()}>{options ? 'Refresh compatible validators' : fieldPath ? 'Add a validator' : 'Find compatible validators'}</Button>
    {loading && <Typography role="status">Checking compatible fields and package capabilities…</Typography>}
    {error && <Alert severity="error">{error} Check the structure fields and retry. Your mappings have not changed.</Alert>}
    {options && !current && <Alert severity="warning">Fields changed. Refresh compatible validators before choosing new field mappings.</Alert>}
    {data && fieldPath && <>
      <Autocomplete options={fieldCapabilities} groupBy={cap => cap.metadata.origin === 'custom_agent' ? 'My custom validators' : 'Built-in & package validators'} value={chosen ?? null} getOptionLabel={cap => cap.metadata.display_name || friendlyValidatorName(cap.capability_ref.binding_id)}
        isOptionEqualToValue={(a,b) => capabilityKey(a) === capabilityKey(b)} getOptionDisabled={cap => !cap.selectable}
        disabled={disabled || loading} onChange={(_, cap) => { setSelectedCapability(cap ? capabilityKey(cap) : null); setSelectedSlot('') }}
        noOptionsText="No compatible validator in the loaded results. Try another search or load more validators."
        renderInput={params => <TextField {...params} label="Search built-in and custom validators" placeholder="For example, gene or reference" />}
        renderOption={(props, cap) => <li {...props} key={capabilityKey(cap)}><Box><Typography fontWeight={600}>{cap.metadata.display_name || friendlyValidatorName(cap.capability_ref.binding_id)}</Typography><Typography variant="body2">{cap.metadata.description || cap.metadata.reason || cap.diagnostics.join(' ')}</Typography></Box></li>} />
      <Typography variant="body2" color="text.secondary">Choose a validator for the meaning of this detail. A text answer is not automatically a gene or paper reference.</Typography>
      {chosen && <><TextField select label={`Use “${fieldName || selectedPath}” as`} value={selectedSlot} disabled={disabled || loading} onChange={event => setSelectedSlot(event.target.value)}>
        {Object.entries(chosen.input_paths).filter(([, paths]) => paths.includes(selectedPath) || paths.includes(`${selectedPath}[]`)).map(([slot]) => <MenuItem key={slot} value={slot}>{friendlyValidatorName(slot)}</MenuItem>)}
      </TextField><Button variant="contained" disabled={disabled || loading || !chosen.selectable || !selectedSlot} onClick={() => { add(chosen, selectedSlot); setSelectedCapability(null); setSelectedSlot('') }}>Attach validator</Button></>}
      {data.next_cursor && <Button disabled={disabled || loading} onClick={() => void load(data.next_cursor ?? undefined)}>Load more validators</Button>}
    </>}
    {data && !fieldPath && <>
      <TextField select label="Find validators for canonical field" value={data.fields.some((field) => field.path === selectedPath) ? selectedPath : ''}
        disabled={disabled || loading} onChange={(event) => setSelectedPath(event.target.value)}>
        <MenuItem value="">Choose a field</MenuItem>
        {data.fields.map((field) => <MenuItem key={field.path} value={field.path}>{field.display_name} · {field.path}{field.array_domains.length ? ' · each list item' : ''}</MenuItem>)}
      </TextField>
      {selectedPath && data.capabilities.flatMap((cap) => Object.entries(cap.input_paths)
        .filter(([, paths]) => paths.includes(selectedPath)).map(([slot]) => <Stack key={`${capabilityKey(cap)}:${slot}`} spacing={0.5}>
          <Typography>{cap.metadata.display_name || cap.capability_ref.binding_id} · {cap.state === 'under_development' ? 'Under development' : 'Active'} · input: {slot}</Typography>
          {cap.diagnostics.map((reason) => <Typography key={reason} variant="body2">{reason}</Typography>)}
          <Button disabled={disabled || loading || !cap.selectable} onClick={() => add(cap, slot)}>Map field to {slot} · {cap.metadata.display_name || cap.capability_ref.binding_id}</Button>
        </Stack>))}
      {selectedPath && !data.capabilities.some((cap) => cap.selectable && Object.values(cap.input_paths).some((paths) => paths.includes(selectedPath)))
        && <Typography>No available compatible semantic capability for this field in the loaded page(s). Structural validation still applies.</Typography>}
      {data.next_cursor && <Button disabled={disabled || loading} onClick={() => void load(data.next_cursor ?? undefined)}>Load more compatible validators</Button>}
    </>}
    {mappings.map((mapping, index) => {
      if (fieldPath && !mappingUsesField(mapping, fieldPath)) return null
      const cap = data?.capabilities.find((cap) => capabilityKey(cap) === capabilityKey(mapping))
      const reuse = cap?.metadata.custom_profile_reuse
      const prefix = `validator_mappings[${index}]`
      const errors = issues.filter((issue) => issue.path.startsWith(prefix))
      const fieldError = (path: string) => errors.filter((issue) => issue.path === path).map((issue) => issue.message).join(' ')
      const pathsForMode = (paths: string[], excludedInput?: string, excludedOutput?: string) => paths.filter((path) => {
        const field = data?.fields.find((field) => field.path === path)
        const others = [...Object.entries(mapping.inputs).filter(([slot]) => slot !== excludedInput).map(([, source]) => source.field_path),
          ...Object.entries(mapping.outputs).filter(([slot]) => slot !== excludedOutput).map(([, destination]) => destination)]
        const domains = new Set(others.flatMap((path) => data?.fields.find((field) => field.path === path)?.array_domains ?? []))
        if (mapping.mode !== 'per_element') return field?.array_domains.length === 0
        // Shared root inputs (for example a bounded provider) are reused for
        // every item. Only per-element write destinations must be inside it.
        if (field?.array_domains.length === 0) return excludedInput !== undefined
        return field?.array_domains.length === 1 && domains.size <= 1
          && (!domains.size || domains.has(field.array_domains[0]))
      })
      return <Stack component="fieldset" id={`profile-${prefix}`} tabIndex={-1} disabled={disabled} key={mapping.mapping_id} spacing={1.5} sx={{ minWidth: 0, border: fieldPath ? 0 : undefined, borderColor: 'divider', p: fieldPath ? 0 : undefined, borderTop: fieldPath ? 1 : undefined, pt: fieldPath ? 2 : undefined }}>
        <Typography component="legend">{cap?.metadata.display_name || friendlyValidatorName(mapping.capability_ref.binding_id)}{!fieldPath && ` · ${mapping.mapping_id}`}</Typography>
        {!fieldPath && <Typography variant="body2">Package {mapping.capability_ref.package_id} {mapping.capability_ref.package_version}; binding {mapping.capability_ref.binding_id}. {cap ? (cap.state === 'under_development' ? 'Under development' : 'Active') : 'Capability details not loaded.'}</Typography>}
        <Stack spacing={0.5} sx={{ overflowWrap: 'anywhere' }}>
          {Object.entries(mapping.inputs).map(([slot, source]) => <Typography key={slot} variant="body2">{friendlyValidatorName(slot)}: {source.source === 'context' ? 'package-owned context' : source.source === 'constant' ? `fixed value — ${constantSummary(source.value)}` : data?.fields.find(field => field.path === source.field_path)?.display_name || source.field_path || 'field not selected'}</Typography>)}
          {Object.entries(mapping.outputs).map(([slot, path]) => <Typography key={slot} variant="body2">Output {slot}: {path}</Typography>)}
          <Typography variant="body2">Unresolved: {POLICY_LABELS[mapping.policy.unresolved]}; {mapping.policy.blocks_readiness ? 'blocks readiness/export' : 'does not block readiness/export'}.</Typography>
        </Stack>
        {(!cap || !cap.selectable || cap.fingerprint !== mapping.capability_fingerprint) && <Alert severity="warning">
          {!cap ? 'Load or refresh the capability pages to inspect this mapping. If its exact version is unavailable, remove it or explicitly create a replacement.'
            : cap.fingerprint !== mapping.capability_fingerprint ? 'The capability contract changed. This saved mapping is not automatically repinned; remove it and explicitly map the current capability.'
              : cap.diagnostics.join(' ')}
        </Alert>}
        {fieldPath && <Button sx={{ alignSelf: 'flex-start' }} onClick={() => setExpanded(old => ({ ...old, [mapping.mapping_id]: !old[mapping.mapping_id] }))}>{expanded[mapping.mapping_id] ? 'Done editing validator' : 'Edit validator settings'}</Button>}
        {reuse && (!fieldPath || expanded[mapping.mapping_id]) && <>
          <TextField select label={fieldPath ? 'When to run this validator' : `Mapping mode · ${mapping.mapping_id}`} value={mapping.mode ?? 'whole'} disabled={disabled} onBlur={onValidate}
            onChange={(event) => patch(index, { ...mapping, mode: event.target.value as 'whole' | 'per_element' })}>
            <MenuItem value="whole">Once for the record (whole values)</MenuItem>
            {(reuse.supports_element_fanout || mapping.mode === 'per_element') && <MenuItem value="per_element" disabled={!reuse.supports_element_fanout}>Once for each item in one shared list</MenuItem>}
          </TextField>
          {reuse.requires_evidence && <Typography variant="body2">This capability requires the record’s evidence context.</Typography>}
          {reuse.required_any_inputs.map((slots, i) => <Typography key={i} variant="body2">Map at least one of: {slots.join(', ')}.</Typography>)}
          {Object.entries(reuse.inputs).map(([slot, input]) => {
            const source = mapping.inputs[slot]
            const label = fieldPath ? friendlyValidatorName(slot) : `${slot} · ${mapping.mapping_id}`
            const choices = pathsForMode(cap.input_paths[slot] ?? [], slot)
            const change = (next: typeof source | undefined) => {
              const inputs = { ...mapping.inputs }
              if (next) inputs[slot] = next
              else delete inputs[slot]
              patch(index, { ...mapping, inputs })
            }
            return <Stack key={slot} spacing={1}>
              <TextField select label={`Input source · ${label}`} value={source?.source ?? (source ? 'field' : '')} disabled={disabled} onBlur={onValidate}
                error={Boolean(fieldError(`${prefix}.inputs.${slot}`))} helperText={fieldError(`${prefix}.inputs.${slot}`) || (input.required ? 'Required input' : 'Optional input')}
                onChange={(event) => change(event.target.value === 'field' ? { source: 'field', field_path: '' }
                  : event.target.value === 'constant' ? { source: 'constant', value: undefined }
                    : event.target.value === 'context' ? { source: 'context' } : undefined)}>
                <MenuItem value="">Not mapped</MenuItem>
                {input.allow_field && <MenuItem value="field">{fieldPath ? 'A detail in this item' : 'Canonical profile field'}</MenuItem>}
                {input.allow_constant && <MenuItem value="constant">{fieldPath ? 'A fixed value' : 'Fixed typed value'}</MenuItem>}
                {input.context_selector && <MenuItem value="context">{fieldPath ? 'Record context supplied automatically' : 'Package-owned record context'}</MenuItem>}
              </TextField>
              {source && (source.source ?? 'field') === 'field' && <TextField select label={`Input field · ${label}`} value={source.field_path ?? ''} disabled={disabled} onBlur={onValidate}
                error={Boolean(fieldError(`${prefix}.inputs.${slot}.field_path`))} helperText={fieldError(`${prefix}.inputs.${slot}.field_path`) || 'Only type, requiredness, nullability and cardinality-compatible canonical fields.'}
                onChange={(event) => change({ source: 'field', field_path: event.target.value })}>
                <MenuItem value="">Choose a field</MenuItem>
                {source.field_path && !choices.includes(source.field_path) && <MenuItem value={source.field_path} disabled>{source.field_path} · incompatible or no longer declared</MenuItem>}
                {choices.map((path) => <MenuItem key={path} value={path}>{fieldPath ? data?.fields.find(field => field.path === path)?.display_name || path : path}</MenuItem>)}
              </TextField>}
              {source?.source === 'constant' && <ProfileConstantInput label={`Fixed value · ${label}`} schema={input.value_schema} nullable={input.nullable}
                value={source.value} disabled={disabled} onChange={(next) => change({ source: 'constant', value: next })} onBlur={onValidate} />}
              {input.context_selector && <Typography variant="body2">Fixed context supplied by the package: {Object.entries(input.context_selector).map(([key, value]) => `${key}: ${String(value)}`).join('; ')}. The context selector cannot be edited.</Typography>}
              {Object.values(reuse.provider_input_slots).includes(slot) && <Typography variant="body2">Provider identity must be a required, non-null bounded choice or permitted fixed value: {cap.metadata.group_scope?.allowed_provider_values.join(', ') || 'see package policy'}. {cap.metadata.group_scope?.allow_cross_provider ? 'Cross-provider values permitted by the package.' : 'One unambiguous provider only.'}</Typography>}
            </Stack>
          })}
          {Object.entries(reuse.outputs).map(([slot, output]) => {
            const destination = mapping.outputs[slot] ?? ''
            const choices = pathsForMode(cap.output_paths[slot] ?? [], undefined, slot)
            return <TextField select key={slot} label={fieldPath ? `Put ${friendlyValidatorName(slot).toLowerCase()} in` : `Write ${slot} to · ${mapping.mapping_id}`} value={destination} disabled={disabled} onBlur={onValidate}
              error={Boolean(fieldError(`${prefix}.outputs.${slot}`))} helperText={fieldError(`${prefix}.outputs.${slot}`) || `Package result: ${output.result_path}. Choose a declared destination; overlapping writes are rejected.`}
              onChange={(event) => {
                const outputs = { ...mapping.outputs }
                if (event.target.value) outputs[slot] = event.target.value
                else delete outputs[slot]
                patch(index, { ...mapping, outputs })
              }}>
              <MenuItem value="">Do not write this result</MenuItem>
              {destination && !choices.includes(destination) && <MenuItem value={destination} disabled>{destination} · incompatible or no longer declared</MenuItem>}
              {choices.map((path) => <MenuItem key={path} value={path}>{fieldPath ? data?.fields.find(field => field.path === path)?.display_name || path : path}</MenuItem>)}
            </TextField>
          })}
          <TextField select label={fieldPath ? 'If validation cannot confirm an answer' : `If unresolved · ${mapping.mapping_id}`} value={mapping.policy.unresolved} disabled={disabled} onBlur={onValidate}
            onChange={(event) => patch(index, { ...mapping, policy: { ...mapping.policy, unresolved: event.target.value as ProfileValidatorMapping['policy']['unresolved'] } })}>
            {reuse.policy.unresolved_allowed.map((policy) => <MenuItem key={policy} value={policy}>{POLICY_LABELS[policy]}</MenuItem>)}
            {!reuse.policy.unresolved_allowed.includes(mapping.policy.unresolved) && <MenuItem value={mapping.policy.unresolved} disabled>{POLICY_LABELS[mapping.policy.unresolved]} · no longer allowed</MenuItem>}
          </TextField>
          <TextField select label={fieldPath ? 'Effect on readiness and export' : `Readiness and export · ${mapping.mapping_id}`} value={String(mapping.policy.blocks_readiness)} disabled={disabled} onBlur={onValidate}
            onChange={(event) => patch(index, { ...mapping, policy: { ...mapping.policy, blocks_readiness: event.target.value === 'true' } })}>
            {reuse.policy.readiness_allowed.map((blocks) => <MenuItem key={String(blocks)} value={String(blocks)}>{blocks ? 'Blocks readiness/export until resolved' : 'Does not block readiness/export'}</MenuItem>)}
            {!reuse.policy.readiness_allowed.includes(mapping.policy.blocks_readiness) && <MenuItem value={String(mapping.policy.blocks_readiness)} disabled>Saved policy is no longer allowed</MenuItem>}
          </TextField>
        </>}
        {errors.map((issue, i) => <Typography key={i} role="alert" color="error">{issue.message}</Typography>)}
        <Button disabled={disabled} color="error" onClick={() => onChange({ ...value, validator_mappings: mappings.filter((_, i) => i !== index) })}>{fieldPath ? 'Remove validator' : `Remove mapping ${mapping.mapping_id}`}</Button>
      </Stack>
    })}
    {mappings.length > 0 && <Typography variant="body2">Review every input, destination and unresolved policy before explicit Save. Saving creates an immutable revision; this panel never runs a validator.</Typography>}
  </Stack>
}
