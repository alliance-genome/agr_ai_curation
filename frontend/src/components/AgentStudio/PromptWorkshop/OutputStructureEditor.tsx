import { useEffect, useRef, useState } from 'react'
import {
  Alert, Box, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControlLabel, MenuItem, Stack, TextField, Typography,
} from '@mui/material'
import type { GenericProfileContract, GenericProfileValueSchema, ProfileMappingDiagnostic } from '@/services/genericProfileService'
import ProfileValidatorEditor from './ProfileValidatorEditor'
import {
  addProfileField, childSchema, duplicateProfileField, friendlyProfileKind, moveProfileField,
  PROFILE_KIND_LABELS, profileExampleRecord, profileExampleValue, profileFieldRows,
  removeProfileField, schemaForKind, updateProfileField,
  type FriendlyProfileKind, type ProfileFieldAddress,
} from './profileEditorModel'

export interface OutputStructureEditorProps {
  value: GenericProfileContract
  onChange: (value: GenericProfileContract) => void
  onValidate: () => void
  issues: ProfileMappingDiagnostic[]
  validating?: boolean
  disabled?: boolean
}

function ExampleValue({ schema }: { schema: GenericProfileValueSchema }) {
  if (schema.kind === 'array') return <Box sx={{ pl: 1.5, borderLeft: 1, borderColor: 'divider' }}>
    <Typography variant="caption">Example list item</Typography><ExampleValue schema={schema.items} />
  </Box>
  if (schema.kind === 'object') return <Box component="dl" sx={{ m: 0 }}>
    {schema.fields.map((field, index) => <Box key={index} sx={{ mb: 1 }}>
      <Typography component="dt" variant="body2" fontWeight={600}>{field.display_name || field.key || 'Unnamed field'}</Typography>
      <Box component="dd" sx={{ m: 0, pl: 1 }}><ExampleValue schema={field.value_schema} /></Box>
    </Box>)}
    {schema.fields.length === 0 ? <Typography variant="body2" color="text.secondary">No fields yet</Typography> : null}
  </Box>
  return <Typography variant="body2" sx={{ overflowWrap: 'anywhere' }}>{String(profileExampleValue(schema))}</Typography>
}

function ValueSchemaEditor({ schema, onChange, onChangeKind, onBlur, path, issueText, disabled, label = 'Value kind' }: {
  schema: GenericProfileValueSchema
  onChange: (schema: GenericProfileValueSchema) => void
  onChangeKind: (schema: GenericProfileValueSchema) => void
  onBlur: () => void
  path: string
  issueText: (path: string) => string
  disabled: boolean
  label?: string
}) {
  return <Stack spacing={1.5}>
    <TextField select disabled={disabled} id={`profile-${path}`} label={label} value={friendlyProfileKind(schema)} onBlur={onBlur}
      error={Boolean(issueText(path) || issueText(`${path}.kind`))} helperText={issueText(path) || issueText(`${path}.kind`) || undefined}
      onChange={(event) => onChangeKind(schemaForKind(event.target.value as FriendlyProfileKind))}>
      {Object.entries(PROFILE_KIND_LABELS).map(([kind, text]) => <MenuItem key={kind} value={kind}>{text}</MenuItem>)}
    </TextField>
    {schema.kind === 'enum' ? <TextField id={`profile-${path}.values`} label="Allowed choices — one per line" multiline minRows={3}
      error={Boolean(issueText(`${path}.values`))} helperText={issueText(`${path}.values`) || undefined}
      value={schema.values.join('\n')} onChange={(event) => onChange({ kind: 'enum', values: event.target.value ? event.target.value.split('\n') : [] })} onBlur={onBlur} /> : null}
    {schema.kind === 'array' ? <ValueSchemaEditor schema={schema.items} disabled={disabled} label={`List item ${label.toLowerCase()}`} path={`${path}.items`} issueText={issueText}
      onChange={(items) => onChange({ ...schema, items })} onChangeKind={(items) => onChangeKind({ ...schema, items })} onBlur={onBlur} /> : null}
  </Stack>
}

/** Controlled editor: all scientific values live in useWorkshopDraft, not here. */
export default function OutputStructureEditor({ value, onChange, onValidate, issues, validating = false, disabled = false }: OutputStructureEditorProps) {
  const [selected, setSelected] = useState<ProfileFieldAddress>([0])
  const [showTechnical, setShowTechnical] = useState(false)
  const [pendingRemoval, setPendingRemoval] = useState<ProfileFieldAddress | null>(null)
  const [pendingType, setPendingType] = useState<{ address: ProfileFieldAddress; schema: GenericProfileValueSchema } | null>(null)
  const detailsRef = useRef<HTMLInputElement>(null)
  const [focusIssue, setFocusIssue] = useState<string | null>(null)
  const rows = profileFieldRows(value)
  const row = rows.find((candidate) => candidate.address.join('.') === selected.join('.')) ?? rows[0]
  const field = row?.field
  const fieldIssues = row ? issues.filter((issue) => issue.path.startsWith(row.schemaPath)) : []
  const issueText = (path: string) => issues.filter((issue) => issue.path === path || issue.path.startsWith(`${path}[`)).map((issue) => issue.message).join(' ')
  useEffect(() => {
    if (!focusIssue) return
    const mappingPath = focusIssue.match(/^validator_mappings\[\d+\]/)?.[0]
    const target = document.getElementById(`profile-${focusIssue}`)
      ?? document.getElementById(`profile-${focusIssue.replace(/\[\d+\]$/, '').replace(/\.kind$/, '')}`)
      ?? (mappingPath ? document.getElementById(`profile-${mappingPath}`) : detailsRef.current)
    let parent = target?.parentElement
    while (parent) {
      if (parent instanceof HTMLDetailsElement) parent.open = true
      parent = parent.parentElement
    }
    target?.focus()
    setFocusIssue(null)
  }, [focusIssue, row?.schemaPath])
  const patchField = (patch: Parameters<typeof updateProfileField>[2]) => {
    if (row) onChange(updateProfileField(value, row.address, patch))
  }
  const select = (address: ProfileFieldAddress) => {
    setSelected(address)
    detailsRef.current?.focus()
  }
  const add = (parent: ProfileFieldAddress) => {
    const next = addProfileField(value, parent)
    const children = parent.length
      ? childSchema(profileFieldRows(next).find((entry) => entry.address.join('.') === parent.join('.'))!.field.value_schema)!.fields
      : next.fields
    onChange(next)
    select([...parent, children.length - 1])
  }
  const selectIssue = (path: string) => {
    const target = [...rows].reverse().find((entry) => path.startsWith(entry.schemaPath))
    if (target) select(target.address)
    else document.getElementById(`profile-${path}`)?.focus()
    setFocusIssue(path)
  }
  const siblingCount = row && row.address.length > 1
    ? childSchema(rows.find((entry) => entry.address.join('.') === row.address.slice(0, -1).join('.'))!.field.value_schema)!.fields.length
    : value.fields.length

  return <Stack spacing={2}>
    <Alert severity="info">
      Only defined fields are accepted. Optional fields may be absent. To change the structure, edit this draft and explicitly save a new revision.
      These records remain Generic Objects, not Alliance LinkML or submission objects.
    </Alert>
    <Box component="fieldset" disabled={disabled} sx={{ border: 0, p: 0, m: 0, minWidth: 0 }}>
      <Typography component="legend" variant="h6">Describe one output record</Typography>
      <Stack spacing={2} sx={{ mt: 1 }}>
        <TextField id="profile-name" label="Structure name" value={value.name} required
          error={Boolean(issueText('name'))} helperText={issueText('name') || undefined}
          onChange={(event) => onChange({ ...value, name: event.target.value })} onBlur={onValidate} />
        <TextField id="profile-description" label="What does one record represent?" value={value.description ?? ''} multiline minRows={2}
          error={Boolean(issueText('description'))}
          helperText={issueText('description') || 'Describe the information you want to collect together in one record.'}
          onChange={(event) => onChange({ ...value, description: event.target.value })} onBlur={onValidate} />
        <TextField id="profile-semantic_class" label="Record class" value={value.semantic_class} required
          error={Boolean(issueText('semantic_class'))}
          helperText={issueText('semantic_class') || 'A short name for this kind of record. Saving fixes this class for the revision.'}
          onChange={(event) => onChange({ ...value, semantic_class: event.target.value })} onBlur={onValidate} />
      </Stack>
    </Box>
    <Box sx={{ border: 1, borderColor: 'divider', p: 1.5, borderRadius: 1 }}>
      <Typography variant="subtitle2">Locked platform fields</Typography>
      <Typography variant="body2" color="text.secondary">Record label, object identity, semantic class, evidence and provenance are managed by the platform. Custom fields below live inside attributes.</Typography>
    </Box>
    <Box aria-live="polite">
      {validating ? <Typography role="status">Checking this draft…</Typography> : null}
      {issues.length ? <Alert severity="error" role="alert">
        <Typography variant="subtitle2">Review these issues. Your edits are preserved.</Typography>
        <Box component="ul" sx={{ m: 0, pl: 2 }}>{issues.map((issue, index) => <li key={index}>
          <Button size="small" onClick={() => selectIssue(issue.path)} sx={{ textAlign: 'left', textTransform: 'none' }}>
            {issue.path || 'Structure'}: {issue.message}
          </Button>
        </li>)}</Box>
      </Alert> : null}
    </Box>
    <Box sx={{ display: 'grid', gridTemplateColumns: { xs: 'minmax(0, 1fr)', lg: 'minmax(180px, 1fr) minmax(260px, 2fr) minmax(180px, 1fr)' }, gap: 2, alignItems: 'start' }}>
      <Stack spacing={1}>
        <Typography component="h3" variant="subtitle1">Custom fields</Typography>
        {rows.length === 0 ? <Typography variant="body2">Start by adding a field you want in each record.</Typography> : null}
        <Box component="ul" aria-label="Custom fields" sx={{ m: 0, p: 0, listStyle: 'none' }}>
          {rows.map((entry) => <Box component="li" key={entry.address.join('.')} sx={{ pl: entry.depth * 1.5, mb: 0.5 }}>
            <Button fullWidth variant={row === entry ? 'outlined' : 'text'} onClick={() => select(entry.address)}
              aria-pressed={row === entry} sx={{ justifyContent: 'flex-start', textAlign: 'left', textTransform: 'none', minHeight: 44 }}>
              <Box><Typography variant="body2">{entry.field.display_name || entry.field.key || 'Unnamed field'}</Typography>
                <Typography variant="caption" color="text.secondary">{PROFILE_KIND_LABELS[friendlyProfileKind(entry.field.value_schema)]}</Typography></Box>
            </Button>
          </Box>)}
        </Box>
        <Button variant="outlined" disabled={disabled} onClick={() => add([])}>Add field</Button>
      </Stack>
      <Box component="fieldset" disabled={disabled} sx={{ border: 1, borderColor: 'divider', p: 2, m: 0, minWidth: 0, borderRadius: 1 }}>
        <Typography component="legend" variant="subtitle1">Field details</Typography>
        {field && row ? <Stack spacing={1.5}>
          <TextField id={`profile-${row.schemaPath}.display_name`} inputRef={detailsRef} label="Field name" value={field.display_name ?? ''}
            error={Boolean(issueText(`${row.schemaPath}.display_name`))}
            helperText={issueText(`${row.schemaPath}.display_name`) || undefined}
            onChange={(event) => patchField({ display_name: event.target.value })} onBlur={onValidate} />
          <TextField id={`profile-${row.schemaPath}.description`} label="What does this field mean?" value={field.description ?? ''} multiline minRows={2}
            error={Boolean(issueText(`${row.schemaPath}.description`))}
            helperText={issueText(`${row.schemaPath}.description`) || undefined}
            onChange={(event) => patchField({ description: event.target.value })} onBlur={onValidate} />
          <ValueSchemaEditor schema={field.value_schema} disabled={disabled} path={`${row.schemaPath}.value_schema`} issueText={issueText} onChange={(schema) => patchField({ value_schema: schema })}
            onBlur={onValidate} onChangeKind={(schema) => {
              let previous = field.value_schema
              while (previous.kind === 'array') previous = previous.items
              if (childSchema(field.value_schema)?.fields.length || previous.kind === 'enum') setPendingType({ address: row.address, schema })
              else patchField({ value_schema: schema })
            }} />
          {childSchema(field.value_schema) ? <Button onClick={() => add(row.address)}>Add child field</Button> : null}
          <FormControlLabel control={<Checkbox checked={field.required ?? false} onChange={(_, required) => patchField({ required })} onBlur={onValidate} />} label="Required — every record must include this field" />
          <FormControlLabel control={<Checkbox checked={field.nullable ?? false} onChange={(_, nullable) => patchField({ nullable })} onBlur={onValidate} />} label="Allow an explicit unknown value (null)" />
          <Typography variant="caption">Optional means the field can be absent. Allowing unknown means it can be present with no known value; these are separate choices.</Typography>
          <Box component="details">
            <Typography component="summary" sx={{ cursor: 'pointer' }}>Technical key and source labels</Typography>
            <Stack spacing={1.5} sx={{ mt: 1 }}>
              <TextField id={`profile-${row.schemaPath}.key`} label="Output key" value={field.key}
                error={Boolean(issueText(`${row.schemaPath}.key`))} helperText={issueText(`${row.schemaPath}.key`) || `Canonical output: ${row.canonicalPath}`}
                onChange={(event) => patchField({ key: event.target.value })} onBlur={onValidate} />
              <TextField id={`profile-${row.schemaPath}.source_labels`} label="Synonyms / source labels (not output fields)" multiline minRows={2} value={(field.source_labels ?? []).join('\n')}
                error={Boolean(issueText(`${row.schemaPath}.source_labels`))}
                helperText={issueText(`${row.schemaPath}.source_labels`) || 'One per line. These help recognize source headings; they never add output keys.'}
                onChange={(event) => patchField({ source_labels: event.target.value ? event.target.value.split('\n') : [] })} onBlur={onValidate} />
            </Stack>
          </Box>
          {fieldIssues.map((issue, index) => <Typography key={index} role="alert" variant="body2" color="error">{issue.message}</Typography>)}
          <Stack direction="row" useFlexGap flexWrap="wrap" gap={1}>
            <Button onClick={() => { onChange(duplicateProfileField(value, row.address)); setSelected([...row.address.slice(0, -1), row.address.at(-1)! + 1]) }}>Duplicate</Button>
            {([-1, 1] as const).map((direction) => <Button key={direction}
              disabled={row.address.at(-1)! + direction < 0 || row.address.at(-1)! + direction >= siblingCount} onClick={() => {
              const next = moveProfileField(value, row.address, direction)
              if (next.fields !== value.fields) { onChange(next); setSelected([...row.address.slice(0, -1), row.address.at(-1)! + direction]) }
            }}>Move {direction === -1 ? 'up' : 'down'}</Button>)}
            <Button color="error" onClick={() => setPendingRemoval(row.address)}>Remove field</Button>
          </Stack>
        </Stack> : <Typography variant="body2">Add a field to describe its values here.</Typography>}
      </Box>
      <Stack spacing={1}>
        <Typography component="h3" variant="subtitle1">Example record</Typography>
        <Chip label="Placeholder data, not paper evidence" size="small" sx={{ height: 'auto', '& .MuiChip-label': { whiteSpace: 'normal' } }} />
        <Typography variant="caption">Includes optional fields for illustration. Required and unknown-value rules remain as configured.</Typography>
        <ExampleValue schema={{ kind: 'object', fields: value.fields }} />
        <Button onClick={() => setShowTechnical(!showTechnical)} aria-expanded={showTechnical}>{showTechnical ? 'Hide' : 'Show'} technical JSON preview</Button>
        {showTechnical ? <Box component="pre" sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontSize: 12 }}>{JSON.stringify(profileExampleRecord(value), null, 2)}</Box> : null}
      </Stack>
    </Box>
    <ProfileValidatorEditor value={value} onChange={onChange} onValidate={onValidate} issues={issues} disabled={disabled} />
    <Stack component="section" aria-label="Review before saving" spacing={1}>
      <Typography component="h3" variant="subtitle1">Review before saving</Typography>
      <Typography>{value.name || 'Unnamed structure'} · Record class: {value.semantic_class || 'Not set'}</Typography>
      <Button disabled={disabled} onClick={() => selectIssue('name')}>Change structure basics</Button>
      {rows.map((entry) => <Box key={entry.schemaPath} sx={{ overflowWrap: 'anywhere' }}>
        <Typography variant="body2">
          {entry.field.display_name || entry.field.key} · {PROFILE_KIND_LABELS[friendlyProfileKind(entry.field.value_schema)]} · {entry.field.required ? 'Required' : 'May be absent'} · {entry.field.nullable ? 'Explicit unknown (null) allowed' : 'Explicit unknown (null) not allowed'}
        </Typography>
        <Typography variant="body2">Synonyms / source labels (not output fields): {entry.field.source_labels?.join(', ') || 'None'}</Typography>
        <Button disabled={disabled} onClick={() => selectIssue(`${entry.schemaPath}.display_name`)}>Change {entry.field.display_name || entry.field.key}</Button>
      </Box>)}
      <Typography variant="body2">{value.validator_mappings?.length || 0} optional semantic validator mappings. Structure conformance is always enforced.</Typography>
      {value.validator_mappings?.map((mapping, index) => <Box key={mapping.mapping_id}>
        <Typography variant="body2">{mapping.mapping_id} · {mapping.capability_ref.binding_id} · {mapping.policy.unresolved.replaceAll('_', ' ')} · {mapping.policy.blocks_readiness ? 'Blocks readiness/export' : 'Does not block readiness/export'}</Typography>
        <Button disabled={disabled} onClick={() => selectIssue(`validator_mappings[${index}]`)}>Change mapping {mapping.mapping_id}</Button>
      </Box>)}
      <Typography variant="body2">Use the Workshop Save button when ready. Saving creates or pins an immutable revision; existing agent and flow consumers keep their saved revisions.</Typography>
    </Stack>
    <Button variant="outlined" disabled={disabled || validating} onClick={onValidate}>Validate structure</Button>
    <Dialog open={pendingRemoval !== null} onClose={() => setPendingRemoval(null)} aria-labelledby="remove-profile-field-title">
      <DialogTitle id="remove-profile-field-title">Remove this field from the draft?</DialogTitle>
      <DialogContent>Its nested fields will also be removed. Saved revisions remain unchanged. Any validator mappings still referencing it must be updated before saving.</DialogContent>
      <DialogActions><Button onClick={() => setPendingRemoval(null)}>Cancel</Button><Button color="error" disabled={disabled} onClick={() => {
        if (pendingRemoval) onChange(removeProfileField(value, pendingRemoval))
        setPendingRemoval(null)
      }}>Remove field</Button></DialogActions>
    </Dialog>
    <Dialog open={pendingType !== null} onClose={() => setPendingType(null)} aria-labelledby="change-profile-kind-title">
      <DialogTitle id="change-profile-kind-title">Replace this value structure?</DialogTitle>
      <DialogContent>The current nested fields or choices will be removed from this draft. Saved revisions are unchanged.</DialogContent>
      <DialogActions><Button onClick={() => setPendingType(null)}>Cancel</Button><Button disabled={disabled} onClick={() => {
        if (pendingType) onChange(updateProfileField(value, pendingType.address, { value_schema: pendingType.schema }))
        setPendingType(null)
      }}>Change kind</Button></DialogActions>
    </Dialog>
  </Stack>
}
