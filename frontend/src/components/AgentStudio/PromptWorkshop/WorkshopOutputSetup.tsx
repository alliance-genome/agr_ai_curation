import { useState } from 'react'
import { Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, MenuItem, Radio, RadioGroup, Stack, TextField, Typography } from '@mui/material'
import type { AgentMetadata } from '@/services/agentStudioService'
import { emptyOutputDraft, type WorkshopOutputDraft } from './workshopOutputDraft'

const outputHelp = {
  profile_bound_generic: {
    title: 'Custom Output Structure',
    summary: 'Choose consistent details to collect from every paper.',
    text: 'Define one type of item, then choose its details and any parts that belong together. The agent follows these saved fields, answer types, and inclusion rules across runs. You can attach supported validators to selected details or parts to resolve identities or validate values. A consistent structure makes results easier to compare and export to CSV, TSV, or JSON. You can revise the structure later. These custom records are not automatically ready for Alliance submission.',
  },
  unprofiled_generic: {
    title: 'Flexible extraction',
    summary: 'Let the agent choose details as it reads; fields may vary between runs.',
    text: 'The agent can create the fields it considers useful for your request. This is useful for quick exploration, chat answers, or CSV, TSV, and JSON exports when the columns do not need to stay consistent. It does not enforce a custom set of fields or apply validators attached to custom fields. General record and evidence rules still apply. Choose Custom Output Structure when you need repeatable fields or field-specific validation.',
  },
  domain: {
    title: 'Packaged domain format',
    summary: 'Use an existing biological data structure, with automatic validation where supported.',
    text: 'Choose an existing structure supplied by the installed curation packages. Its fields are already defined, and automatic validation runs where supported and enabled. Available extraction, validation, and submission support varies by format. If you need details that its structure does not include, use a Custom Output Structure. Selecting a packaged format does not by itself make results ready for submission.',
  },
} as const

interface Props {
  value: WorkshopOutputDraft
  onChange: (value: WorkshopOutputDraft) => void
  agents: Record<string, AgentMetadata>
  onEditStructure: () => void
  onChooseExisting?: () => void
  disabled?: boolean
}

/** Explicit output intent. Domain maturity describes support, never eligibility. */
export default function WorkshopOutputSetup({ value, onChange, agents, onEditStructure, onChooseExisting, disabled = false }: Props) {
  const help = value.mode === 'none' ? null : outputHelp[value.mode]
  const [pendingMode, setPendingMode] = useState<WorkshopOutputDraft['mode'] | null>(null)
  const changeMode = (mode: WorkshopOutputDraft['mode']) => {
    if (disabled || mode === value.mode) return
    if (value.mode === 'profile_bound_generic' && (value.profilePin || value.profileContract?.name || value.profileContract?.fields.length)) {
      setPendingMode(mode)
    } else onChange(emptyOutputDraft(mode))
  }
  const domains = new Map<string, AgentMetadata>()
  for (const agent of Object.values(agents)) {
    if (agent.is_active === false || agent.visible === false) continue
    if (agent.domain_extraction_ref) domains.set(`builder:${agent.domain_extraction_ref.package_id}:${agent.domain_extraction_ref.agent_id}`, agent)
    else if (agent.output_schema_key && agent.domain_envelope) domains.set(agent.output_schema_key, agent)
  }
  const selectedKey = value.domainExtractionRef
    ? `builder:${value.domainExtractionRef.package_id}:${value.domainExtractionRef.agent_id}` : value.schemaKey
  const selectedDomain = domains.get(selectedKey)
  return <Stack component="section" aria-label="Output configuration" spacing={2}>
    <Typography variant="h6" component="h3" id="workshop-output-heading">Output</Typography>
    <RadioGroup aria-labelledby="workshop-output-heading" value={value.mode === 'none' ? 'none' : 'structured'}
      onChange={(_, mode) => changeMode(mode === 'none' ? 'none' : 'profile_bound_generic')}>
      <FormControlLabel disabled={disabled} value="none" control={<Radio />} label="No structured output" />
      <FormControlLabel disabled={disabled} value="structured" control={<Radio />} label="Structured extraction" />
    </RadioGroup>
    {value.mode !== 'none' && <>
      <TextField disabled={disabled} select label="Output format" value={value.mode}
        onChange={(event) => changeMode(event.target.value as WorkshopOutputDraft['mode'])}>
        <MenuItem value="profile_bound_generic">Custom Output Structure</MenuItem>
        <MenuItem value="domain">Packaged domain format</MenuItem>
        <MenuItem value="unprofiled_generic">Flexible extraction</MenuItem>
      </TextField>
      {help && <Alert severity="info">
        <Typography variant="body2" sx={{ fontWeight: 600, mb: 1 }}>{help.summary}</Typography>
        <Typography variant="body2">{help.text}</Typography>
      </Alert>}
      {value.mode === 'domain' && <>
        <TextField disabled={disabled} select label="Domain format" value={selectedKey}
          onChange={(event) => {
            const chosen = domains.get(event.target.value)
            if (chosen?.domain_extraction_ref) onChange({ ...emptyOutputDraft('domain'), domainExtractionRef: structuredClone(chosen.domain_extraction_ref) })
            else onChange({ ...emptyOutputDraft('domain'), schemaKey: event.target.value })
          }}>
          <MenuItem value="">Choose a domain format</MenuItem>
          {selectedKey && !selectedDomain && <MenuItem value={selectedKey}>{value.domainExtractionRef?.domain_pack_id ?? value.schemaKey} (saved format unavailable in catalog)</MenuItem>}
          {[...domains].map(([key, agent]) => <MenuItem key={key} value={key}>
            {agent.domain_envelope?.display_name ?? agent.name} — {agent.domain_envelope?.status ?? 'support details unavailable'}
          </MenuItem>)}
        </TextField>
        <Typography color="text.secondary">Support labels are advisory. Formats under development can still be selected; available checks vary.</Typography>
        {value.domainExtractionRef && <Typography color="text.secondary">This format uses backend builder finalization, not a model-response schema. Keep its matching tools and package access settings; start from the format’s agent template if those tools are not already attached.</Typography>}
      </>}
      {value.mode === 'profile_bound_generic' && <>
        <Typography>{value.profileContract?.name || 'New Output Structure'} · {value.profileContract?.fields.length ?? 0} top-level fields</Typography>
        <Typography color="text.secondary">{value.profilePin ? `Based on saved revision ${value.profilePin.revision}. Changes are not saved until you save the agent.` : 'Not saved yet. Define the details you want to collect; no JSON editing is required.'}</Typography>
        <Button disabled={disabled} variant="outlined" onClick={onEditStructure}>Edit Output Structure</Button>
        {onChooseExisting && <details><summary>Advanced: reuse a saved structure</summary>
          <Button disabled={disabled} onClick={onChooseExisting}>Choose existing Output Structure</Button>
        </details>}
      </>}
    </>}
    <Dialog open={pendingMode !== null} onClose={() => setPendingMode(null)} aria-labelledby="replace-output-title">
      <DialogTitle id="replace-output-title">Change output format?</DialogTitle>
      <DialogContent>This removes the current Output Structure from this draft, including unsaved field edits. Saved revisions are retained.</DialogContent>
      <DialogActions>
        <Button autoFocus onClick={() => setPendingMode(null)}>Keep editing</Button>
        <Button disabled={disabled} onClick={() => { if (!disabled && pendingMode) onChange(emptyOutputDraft(pendingMode)); setPendingMode(null) }}>Change format</Button>
      </DialogActions>
    </Dialog>
  </Stack>
}
