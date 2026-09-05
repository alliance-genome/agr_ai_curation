import { useEffect, useState } from 'react'
import {
  Alert, Box, Button, Checkbox, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControlLabel, RadioGroup, Radio, Table, TableHead, TableBody, TableRow, TableCell, TableContainer, Divider, Breadcrumbs, Stack, TextField, Typography, Tabs, Tab, Popover, IconButton,
} from '@mui/material'
import type { GenericProfileContract, GenericProfileValueSchema, ProfileMappingDiagnostic } from '@/services/genericProfileService'
import InfoOutlined from '@mui/icons-material/InfoOutlined'
import HelpOutline from '@mui/icons-material/HelpOutline'
import Add from '@mui/icons-material/Add'
import './outputStructureEditor.css'
import ProfileValidatorEditor from './ProfileValidatorEditor'
import {
  addProfileField, childSchema, duplicateProfileField, moveProfileField,
  profileExampleValue, profileFieldRows,
  removeProfileField, schemaForKind, updateProfileField, uniqueFieldKey,
  type FriendlyProfileKind, type ProfileFieldAddress,
} from './profileEditorModel'

export interface OutputStructureEditorProps {
  value: GenericProfileContract
  onChange: (value: GenericProfileContract) => void
  onValidate: () => void
  issues: ProfileMappingDiagnostic[]
  onAskAI?: () => void
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

const answerFormats = [
  { kind: 'enum', label: 'Choose from a list', example: 'Example: newly made / obtained elsewhere' },
  { kind: 'number', label: 'Decimal number', example: 'Example: 2.5' },
  { kind: 'string', label: 'Text', example: 'Example: Canton-S' },
  { kind: 'integer', label: 'Whole number', example: 'Example: 12' },
  { kind: 'boolean', label: 'Yes or no', example: 'Example: yes' },
] as const

function ValueSchemaEditor({ schema, onChange, onChangeKind, onBlur, path, issueText, disabled, isPart }: {
  isPart: boolean
  schema: GenericProfileValueSchema
  onChange: (schema: GenericProfileValueSchema) => void
  onChangeKind: (schema: GenericProfileValueSchema) => void
  onBlur: () => void
  path: string
  issueText: (path: string) => string
  disabled: boolean
}) {
  if (schema.kind === 'array') return <Alert severity="info">
    This saved detail allows several answers. The simplified editor supports one answer per detail.
    <Button disabled={disabled} onClick={() => onChangeKind(schema.items)}>Change to one answer</Button>
  </Alert>
  const answer = schema
  const answerPath = path
  return <Stack spacing={2}>
    <>
      <Typography id={`profile-${answerPath}`} tabIndex={-1} fontWeight={600}>What should one answer look like?</Typography>
      <RadioGroup aria-labelledby={`profile-${answerPath}`} value={answer.kind} onChange={(_, kind) => onChangeKind(schemaForKind(kind as FriendlyProfileKind))} onBlur={onBlur}>
        {answerFormats.map((format) => <FormControlLabel key={format.kind} disabled={disabled} value={format.kind} control={<Radio />} sx={{ m: 0, py: 0.75, alignItems: 'flex-start', borderBottom: 1, borderColor: 'divider' }} label={<Box sx={{ py: 0.5 }}><Typography>{format.label}</Typography><Typography variant="body2" color="text.secondary">{format.example}</Typography></Box>} />)}
        {!isPart && <FormControlLabel disabled={disabled} value="object" control={<Radio />} sx={{ m: 0, py: 0.75, alignItems: 'flex-start' }} label={<Box sx={{ py: 0.5 }}><Typography>An answer with several parts</Typography><Typography variant="body2" color="text.secondary">Example: a supplier’s name AND its catalog number</Typography></Box>} />}
      </RadioGroup>
      {answer.kind === 'enum' && <TextField id={`profile-${answerPath}.values`} label="Allowed choices — one per line" multiline minRows={3} disabled={disabled} error={Boolean(issueText(`${answerPath}.values`))} helperText={issueText(`${answerPath}.values`) || 'The agent will choose one of these answers.'} value={answer.values.join('\n')} onChange={(event) => onChange({ kind: 'enum', values: event.target.value ? event.target.value.split('\n') : [] })} onBlur={onBlur} />}

    </>
    {[path, `${path}.kind`, answerPath, `${answerPath}.kind`].filter((entry, index, all) => all.indexOf(entry) === index).map((entry) => issueText(entry) ? <Typography color="error" role="alert" key={entry}>{issueText(entry)}</Typography> : null)}
  </Stack>
}

/** The overview is read-only. Explicit edit actions update the one Workshop draft. */
export default function OutputStructureEditor({ value, onChange, onValidate, issues, validating = false, disabled = false, onAskAI }: OutputStructureEditorProps) {
  const [itemName, setItemName] = useState('')
  const [view, setView] = useState('fields')
  const [selected, setSelected] = useState<ProfileFieldAddress | null>(null)
  const [basics, setBasics] = useState(false)
  const [checks, setChecks] = useState(false)
  const [instructions, setInstructions] = useState(false)
  const [more, setMore] = useState(false)
  const [adding, setAdding] = useState<ProfileFieldAddress | null>(null)
  const [newName, setNewName] = useState('')
  const [help, setHelp] = useState<{ anchor: HTMLElement; title: string; text: string } | null>(null)
  const [pendingRemoval, setPendingRemoval] = useState<ProfileFieldAddress | null>(null)
  const [pendingType, setPendingType] = useState<{ address: ProfileFieldAddress; schema: GenericProfileValueSchema } | null>(null)
  const [focusIssue, setFocusIssue] = useState<string | null>(null)
  const rows = profileFieldRows(value)
  const row = rows.find((entry) => entry.address.join('.') === selected?.join('.'))
  const field = row?.field
  const parent = row && row.address.length > 1
    ? rows.find((entry) => entry.address.join('.') === row.address.slice(0, -1).join('.'))
    : undefined
  const doneLabel = parent ? `Done — back to ${parent.field.display_name || parent.field.key}` : 'Done — back to all details'
  const selectedPath = row?.schemaPath
  useEffect(() => {
    if (!selectedPath) return
    const heading = document.getElementById('collection-detail-heading')
    heading?.focus({ preventScroll: true })
    heading?.scrollIntoView?.({ block: 'start' })
  }, [selectedPath])
  const issueText = (path: string) => issues.filter((issue) => issue.path === path || issue.path.startsWith(`${path}[`)).map((issue) => issue.message).join(' ')
  const edit = (address: ProfileFieldAddress) => { setSelected(address); setAdding(null); setInstructions(false); setMore(false) }
  const closeEditor = () => { setSelected(null); setAdding(null); onValidate() }
  const finishDetail = () => {
    if (parent) { onValidate(); edit(parent.address) }
    else closeEditor()
  }
  const patch = (change: Parameters<typeof updateProfileField>[2]) => { if (row && !disabled) onChange(updateProfileField(value, row.address, change)) }
  const selectIssue = (path: string) => {
    const target = [...rows].reverse().find((entry) => path.startsWith(entry.schemaPath))
    if (target) { edit(target.address); setMore(true); setInstructions(true) }
    else if (path.startsWith('validator_mappings')) setChecks(true)
    else setBasics(true)
    setFocusIssue(path)
  }
  useEffect(() => {
    if (!focusIssue) return
    // Dialog fields mount through a portal; focus after the opening transition.
    const timer = window.setTimeout(() => {
      const target = document.getElementById(`profile-${focusIssue}`)
        ?? document.getElementById(`profile-${focusIssue.replace(/\[\d+\]$/, '').replace(/\.kind$/, '')}`)
        ?? document.getElementById('collection-field-name')
      target?.focus()
      setFocusIssue(null)
    }, 250)
    return () => window.clearTimeout(timer)
  }, [focusIssue])
  const beginAdd = (parent: ProfileFieldAddress) => { setNewName(''); setAdding(parent) }
  const createField = () => {
    if (!newName.trim() || adding === null || disabled) return
    let next = addProfileField(value, adding)
    const siblings = adding.length ? childSchema(profileFieldRows(next).find((entry) => entry.address.join('.') === adding.join('.'))!.field.value_schema)!.fields : next.fields
    const address = [...adding, siblings.length - 1]
    let stem = newName.normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'field'
    // Prefix generated keys: names such as Label, Evidence and Metadata are platform-owned.
    stem = `detail_${stem}`
    next = updateProfileField(next, address, { display_name: newName.trim(), key: uniqueFieldKey(siblings.slice(0, -1), stem) })
    onChange(next)
    setAdding(null)
    // Adding a part keeps the curator with the group; editing its settings is explicit.
    if (adding.length === 0) edit(address)
  }
  const siblingCount = row && row.address.length > 1
    ? childSchema(rows.find((entry) => entry.address.join('.') === row.address.slice(0, -1).join('.'))!.field.value_schema)!.fields.length
    : value.fields.length
  const addDetailForm = adding !== null ? <Box component="form" onSubmit={(event) => { event.preventDefault(); createField() }} sx={{ my: 2, p: 2, bgcolor: 'action.hover' }}>
    <Typography fontWeight={600}>{adding.length ? `Add a part to ${rows.find((entry) => entry.address.join('.') === adding.join('.'))?.field.display_name || 'this answer'}` : 'Add a detail'}</Typography>
    <TextField autoFocus fullWidth sx={{ mt: 2 }} label="New detail name" value={newName} onChange={(event) => setNewName(event.target.value)} placeholder="For example, Supplier name" disabled={disabled} />
    <Stack direction="row" gap={1} sx={{ mt: 1 }}><Button type="submit" disabled={disabled || !newName.trim()}>Add detail</Button><Button onClick={() => setAdding(null)}>Cancel</Button></Stack>
  </Box> : null
  if (!value.name.trim()) return <Stack spacing={3} sx={{ maxWidth: 720, py: 3 }}>
    <Box><Typography component="h2" variant="h5" fontWeight={600}>What do you want to extract?</Typography>
      <Typography color="text.secondary" sx={{ mt: 2 }}>Name the kind of item you want to find in a paper. Next, choose what you want to know about each one.</Typography></Box>
    <TextField autoFocus label="Type of item" placeholder="For example, Reagents, Experiments, or Antibodies" value={itemName} disabled={disabled}
      onChange={(event) => setItemName(event.target.value)} helperText="This name becomes the title of your item type. For example, Reagents." />
    <Stack direction={{ xs: 'column', sm: 'row' }} gap={2}>
      <Button variant="contained" disabled={disabled || !itemName.trim()} onClick={() => {
        const name = itemName.trim()
        const identity = name.normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'custom_item'
        onChange({ ...value, name, semantic_class: value.semantic_class || identity })
      }}>Choose details to collect</Button>
      {onAskAI && <Button disabled={disabled} onClick={onAskAI}>Help me get started with AI Chat</Button>}
    </Stack>
  </Stack>
  return <Stack spacing={3} className="collection-builder" sx={(theme) => ({
    '--collection-secondary': theme.palette.text.secondary,
    '--collection-border': theme.palette.mode === 'dark' ? '#64748b' : '#cbd5e1',
    '--collection-selected': theme.palette.mode === 'dark' ? '#243550' : '#f1f5f9',
    '--collection-accent': theme.palette.primary.main,
  })}>
    <Stack spacing={3} sx={{ display: row ? 'none' : 'flex' }}>
    <Box>
      <Typography component="h2" variant="h5" fontWeight={600}>{value.name}</Typography>
      <Typography color="text.secondary" sx={{ mt: 1, maxWidth: 680 }}>Your agent will create a separate record for each item it finds in a paper. You choose the details to collect about that item.</Typography>
    </Box>
    {onAskAI && <Stack className="collection-ai" direction={{ xs: 'column', sm: 'row' }} justifyContent="space-between" alignItems={{ sm: 'center' }} gap={2}>
      <Box><Typography fontWeight={600}>Tell AI Chat what you need</Typography><Typography color="text.secondary">Describe the details you need, and AI Chat can help build them.</Typography></Box>
      <Button variant="contained" disabled={disabled} onClick={onAskAI}>Build with AI Chat</Button>
    </Stack>}
    <Stack direction="row" alignItems="center" justifyContent="space-between" gap={2}>
      <Box><Typography component="h3" variant="h6">Additional guidance for this item type</Typography>
      <Typography color="text.secondary" sx={{ mt: 0.5 }}>{value.description || 'Add a short description of what counts as an item and what to include or exclude.'}</Typography><Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>The AI uses this during extraction, in addition to your agent prompt and detail instructions. You don’t need to repeat your full prompt here.</Typography></Box>
      <Button disabled={disabled} onClick={() => setBasics(true)}>Edit item description</Button>
    </Stack>
    <Tabs value={view} onChange={(_, next) => setView(next)} aria-label="Collection design">
      <Tab label="Details to collect" value="fields" id="collection-tab-fields" aria-controls="collection-panel-fields" />
      <Tab label="Example record" value="example" id="collection-tab-example" aria-controls="collection-panel-example" />
    </Tabs>
    {validating && <Typography role="status">Checking your fields…</Typography>}
    {issues.length > 0 && <Alert severity="error"><Typography fontWeight={600}>Some settings need attention. Your draft is preserved.</Typography>
      {issues.map((issue, index) => <Button key={index} onClick={() => selectIssue(issue.path)} sx={{ display: 'block', textAlign: 'left' }}>{issue.message}</Button>)}
    </Alert>}
    <Box role="tabpanel" hidden={view !== 'fields'} id="collection-panel-fields" aria-labelledby="collection-tab-fields">
      <Typography component="h3" variant="h6" sx={{ mb: 1 }}>What do you want to know about each item?</Typography>
      <Typography color="text.secondary" sx={{ mb: 3 }}>Add only the details you need. The paper and supporting evidence are kept automatically.</Typography>
      <TableContainer sx={{ position: 'relative', border: 1, borderColor: 'divider', borderRadius: 1 }} tabIndex={0} role="region" aria-label="Details table; scroll horizontally on small screens">
      <Table aria-label="Details to collect" sx={{ minWidth: 560 }}>
        <TableHead sx={{ bgcolor: 'action.hover' }}><TableRow><TableCell sx={{ fontWeight: 700 }}>Detail</TableCell><TableCell sx={{ fontWeight: 700 }}>What to collect</TableCell><TableCell sx={{ fontWeight: 700 }}>Include</TableCell><TableCell><span className="collection-sr-only">Actions</span></TableCell></TableRow></TableHead>
        <TableBody>
        {rows.map((entry) => {
          const name = entry.field.display_name || entry.field.key
          const parent = rows.find((candidate) => candidate.address.join('.') === entry.address.slice(0, -1).join('.'))
          return <TableRow key={entry.schemaPath} sx={{ bgcolor: entry.depth ? 'action.hover' : undefined }}>
            <TableCell component="th" scope="row" sx={{ pl: entry.depth ? 4 : 2 }}><Stack direction="row" alignItems="center" gap={0.5}>
              <Typography fontWeight={600}>{name}</Typography>
              <IconButton aria-label={`About ${name}`} onClick={(event) => setHelp({ anchor: event.currentTarget, title: name, text: entry.field.description || 'No extraction instructions have been added for this field.' })}><InfoOutlined fontSize="small" /></IconButton>
            </Stack>{parent && <Typography variant="body2" color="text.secondary">Part of {parent.field.display_name || parent.field.key}</Typography>}</TableCell>
            <TableCell><Typography>{answerSummary(entry.field.value_schema)}</Typography><Typography variant="body2" color="text.secondary">{answerExample(entry.field.value_schema)}</Typography></TableCell>
            <TableCell><Typography color="text.secondary">{entry.field.required ? (entry.depth ? 'With its parent answer' : 'Every record') : 'When available'}</Typography></TableCell>
            <TableCell><Button disabled={disabled} aria-label={`Edit ${name}`} onClick={() => edit(entry.address)}>Edit</Button></TableCell>
          </TableRow>
        })}
        {rows.length === 0 && <TableRow><TableCell colSpan={4} sx={{ py: 5, textAlign: 'center' }}><Typography fontWeight={600}>No details yet</Typography><Typography color="text.secondary" sx={{ mt: 1 }}>Add your first detail, such as “Stock name”.</Typography></TableCell></TableRow>}
        </TableBody>
      </Table></TableContainer>
      <Button startIcon={<Add />} disabled={disabled} onClick={() => beginAdd([])} sx={{ mt: 2 }}>Add a detail</Button>
      {!row && addDetailForm}
    </Box>
    <Box role="tabpanel" hidden={view !== 'example'} id="collection-panel-example" aria-labelledby="collection-tab-example">
      <Typography color="text.secondary" sx={{ mb: 2 }}>Illustrative values only—not extracted from a paper.</Typography>
      <Box component="dl" sx={{ m: 0 }}>{value.fields.map((item) => <Box className="collection-example-row" key={item.key}>
        <Typography component="dt" fontWeight={600}>{item.display_name || item.key}</Typography>
        <Box component="dd" sx={{ m: 0 }}><ExampleValue schema={item.value_schema} /></Box>
      </Box>)}</Box>
    </Box>
    <Stack direction={{ xs: 'column', sm: 'row' }} gap={2} justifyContent="space-between" className="collection-footer">
      <Typography color="text.secondary">Edits stay in your draft until you save the agent.</Typography>
      <Stack direction="row" gap={1}><Button onClick={() => setChecks(true)}>Additional checks{value.validator_mappings?.length ? ` (${value.validator_mappings.length})` : ''}</Button><Button disabled={disabled || validating} onClick={onValidate}>Check fields</Button></Stack>
    </Stack>
    </Stack>
    <Popover open={Boolean(help)} anchorEl={help?.anchor} onClose={() => setHelp(null)} anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}>
      <Box sx={{ p: 3, maxWidth: 380 }}><Typography fontWeight={600}>{help?.title}</Typography><Typography sx={{ mt: 1, whiteSpace: "pre-line" }}>{help?.text}</Typography><Button onClick={() => setHelp(null)} sx={{ mt: 1 }}>Close</Button></Box>
    </Popover>
    {row && field && <Box>
      <Box id="collection-detail-heading" tabIndex={-1} sx={{ scrollMarginTop: 16, outline: 0 }}><Breadcrumbs aria-label="Detail location" sx={{ mb: 2 }}>
        <Button onClick={closeEditor}>{value.name}</Button>
        {row.address.slice(0, -1).map((_, index) => {
          const ancestor = rows.find((entry) => entry.address.join('.') === row.address.slice(0, index + 1).join('.'))!
          return <Button key={ancestor.schemaPath} onClick={() => edit(ancestor.address)}>{ancestor.field.display_name || ancestor.field.key}</Button>
        })}
        <Typography color="text.primary">{field.display_name || field.key}</Typography>
      </Breadcrumbs></Box>
      <Box sx={{ display: 'grid', gridTemplateColumns: 'minmax(150px, 1fr) minmax(0, 3fr)', gap: 3, '@container workshop (max-width: 680px)': { gridTemplateColumns: 'minmax(0, 1fr)' } }}>
        <Box component="nav" aria-label="Collection details" sx={{ borderRight: 1, borderColor: 'divider', pr: 2 }}>
          <Typography fontWeight={600} sx={{ mb: 1 }}>Details in {value.name}</Typography>
          {rows.map((entry) => <Button key={entry.schemaPath} fullWidth aria-current={entry.schemaPath === row.schemaPath ? 'page' : undefined} onClick={() => edit(entry.address)} sx={{ justifyContent: 'flex-start', textAlign: 'left', pl: 1 + entry.depth * 2, bgcolor: entry.schemaPath === row.schemaPath ? 'action.selected' : undefined }}>{entry.field.display_name || entry.field.key}</Button>)}
        </Box>
        <Box component="section" aria-label={`Edit ${field.display_name || field.key}`}>
          <Stack direction="row" justifyContent="space-between" alignItems="center" gap={2} flexWrap="wrap" sx={{ mb: 3 }}><Typography component="h3" variant="h6">{field.display_name || field.key}</Typography><Stack direction="row" gap={1} flexWrap="wrap"><Button onClick={closeEditor}>Back to all details</Button><Button variant="contained" onClick={finishDetail}>{doneLabel}</Button></Stack></Stack>
          <Typography color="text.secondary" variant="body2" sx={{ mb: 2 }}>Your changes are kept in this draft as you edit. Choose Done when this detail is ready.</Typography>
          <Stack spacing={3}>
        <TextField id="collection-field-name" label="Detail name" error={Boolean(issueText(`${row.schemaPath}.display_name`))} helperText={issueText(`${row.schemaPath}.display_name`)} disabled={disabled} value={field.display_name ?? ''} onChange={(event) => patch({ display_name: event.target.value })} onBlur={onValidate} />
        <ValueSchemaEditor isPart={row.address.length > 1} schema={field.value_schema} disabled={disabled} path={`${row.schemaPath}.value_schema`} issueText={issueText} onChange={(schema) => patch({ value_schema: schema })} onBlur={onValidate} onChangeKind={(schema) => {
          let previous = field.value_schema
          while (previous.kind === 'array') previous = previous.items
          if (field.value_schema.kind === 'array' || childSchema(field.value_schema)?.fields.length || previous.kind === 'enum') setPendingType({ address: row.address, schema })
          else patch({ value_schema: schema })
        }} />
        {row.address.length === 1 && childSchema(field.value_schema) && <Stack spacing={2}>
          <Stack direction="row" alignItems="center" justifyContent="space-between" gap={2} flexWrap="wrap"><Typography component="h4" variant="h6">Parts of {field.display_name || field.key}</Typography><Button variant="outlined" startIcon={<Add />} disabled={disabled} onClick={() => beginAdd(row.address)} sx={{ alignSelf: 'flex-start' }}>{childSchema(field.value_schema)!.fields.length ? 'Add another part' : 'Add the first part'}</Button></Stack>
          <Typography>These parts belong to one answer. For example, “Supplier name” and “Catalog number” describe the same stock. Add them here to keep them together.</Typography>
          <Typography variant="body2" color="text.secondary">Choose which parts to always include with this answer.</Typography>
          <TableContainer sx={{ position: 'relative', border: 1, borderColor: 'divider', borderRadius: 1 }} tabIndex={0} role="region" aria-label="Parts table; scroll horizontally on small screens">
            <Table aria-label={`Parts of ${field.display_name || field.key}`} sx={{ minWidth: 460 }}>
              <TableHead sx={{ bgcolor: 'action.hover' }}><TableRow>
                <TableCell sx={{ fontWeight: 700 }}>Part</TableCell>
                <TableCell sx={{ fontWeight: 700 }}>Answer format</TableCell>
                <TableCell sx={{ fontWeight: 700 }}><Stack direction="row" alignItems="center" gap={0.5}>
                  <span>Always include</span>
                  <IconButton aria-label="Help with Always include" onClick={(event) => setHelp({
                    anchor: event.currentTarget,
                    title: 'What does “Always include” mean?',
                    text: 'Checked: this part must be included whenever the parent answer is included. Unchecked: this part may be left out.\n\nFor example, check Stock number and leave Name unchecked to require a number for each stock answer while allowing the name to be omitted. The parts already belong together because they are in the same group.\n\nThe AI must not invent missing information. An empty answer is allowed only if you enable “Allow an empty answer if the paper doesn’t say” in that part’s settings. Otherwise, a missing value will not pass the structure checks.',
                  })}><HelpOutline fontSize="small" /></IconButton>
                </Stack></TableCell>
                <TableCell><span className="collection-sr-only">Actions</span></TableCell>
              </TableRow></TableHead>
              <TableBody>
                {childSchema(field.value_schema)!.fields.map((part, index) => {
                  const name = part.display_name || part.key
                  const address = [...row.address, index]
                  return <TableRow key={part.key}>
                    <TableCell component="th" scope="row">{name}</TableCell>
                    <TableCell>{answerSummary(part.value_schema)}</TableCell>
                    <TableCell><Checkbox inputProps={{ 'aria-label': `Always include ${name} with this answer` }} disabled={disabled} checked={part.required ?? false} onChange={(_, required) => onChange(updateProfileField(value, address, { required }))} /></TableCell>
                    <TableCell><Button disabled={disabled} onClick={() => edit(address)} aria-label={`Edit part ${name}`}>Edit</Button></TableCell>
                  </TableRow>
                })}
                {childSchema(field.value_schema)!.fields.length === 0 && <TableRow><TableCell colSpan={4} sx={{ py: 4, textAlign: 'center' }}>
                  <Typography fontWeight={600}>No parts yet</Typography>
                  <Typography color="text.secondary">Start with Supplier name, then add Catalog number.</Typography>
                </TableCell></TableRow>}
              </TableBody>
            </Table>
          </TableContainer>
          {addDetailForm}
        </Stack>}
        <Divider />
        <Box><Typography fontWeight={600}>When should this detail be included?</Typography>
          <FormControlLabel control={<Checkbox disabled={disabled} checked={field.required ?? false} onChange={(_, required) => patch({ required })} />} label={parent ? "Include this part whenever the answer is included" : "Ask for this in every record"} />
          <Typography variant="body2" color="text.secondary">Leave unchecked if this detail is only needed when the paper provides it.</Typography>
        </Box>
        <Divider />
        <Box><Stack direction="row" justifyContent="space-between" alignItems="center"><Typography fontWeight={600}>Instructions for this detail</Typography>
          <Button disabled={disabled} onClick={() => setInstructions(!instructions)}>{instructions ? 'Close editor' : field.description ? 'Edit instructions' : 'Add instructions'}</Button></Stack>
          {instructions ? <TextField fullWidth sx={{ mt: 2 }} id={`profile-${row.schemaPath}.description`} label="Instructions for the agent" multiline minRows={3} disabled={disabled} value={field.description ?? ''} placeholder="For example, keep the exact name and punctuation used in the paper." onChange={(event) => patch({ description: event.target.value })} onBlur={onValidate} /> : <Typography color="text.secondary" sx={{ mt: 1 }}>{field.description || 'Optional. Add a rule if the agent needs guidance beyond the detail’s name.'}</Typography>}
        </Box>
        <Divider />
        <Button aria-expanded={more} onClick={() => setMore(!more)} sx={{ alignSelf: 'flex-start' }}>More field options</Button>
        {more && <Stack spacing={2}>
          <FormControlLabel control={<Checkbox disabled={disabled} checked={field.nullable ?? false} onChange={(_, nullable) => patch({ nullable })} />} label="Allow an empty answer if the paper doesn’t say" />
          <Typography color="text.secondary">The detail stays in the record, but its answer can be empty when the paper does not provide the information.</Typography>
          <Stack direction="row" gap={1} flexWrap="wrap">
            <Button disabled={disabled} onClick={() => { onChange(duplicateProfileField(value, row.address)); edit([...row.address.slice(0, -1), row.address.at(-1)! + 1]) }}>Duplicate</Button>
            {([-1, 1] as const).map((direction) => <Button key={direction} disabled={disabled || row.address.at(-1)! + direction < 0 || row.address.at(-1)! + direction >= siblingCount} onClick={() => { onChange(moveProfileField(value, row.address, direction)); setSelected([...row.address.slice(0, -1), row.address.at(-1)! + direction]) }}>Move {direction === -1 ? 'up' : 'down'}</Button>)}
            <Button color="error" disabled={disabled} onClick={() => setPendingRemoval(row.address)}>Remove field</Button>
          </Stack>
        </Stack>}
        {issues.filter((issue) => issue.path.startsWith(row.schemaPath)).map((issue, index) => <Alert severity="error" key={index}>{issue.message}</Alert>)}
          </Stack>
          <Stack direction="row" justifyContent="space-between" alignItems="center" gap={2} flexWrap="wrap" sx={{ mt: 3, pt: 2, borderTop: 1, borderColor: 'divider' }}><Typography color="text.secondary" variant="body2">Your changes are kept in this draft.</Typography><Button variant="contained" onClick={finishDetail}>{doneLabel}</Button></Stack>
        </Box>
      </Box>
    </Box>}
    <Dialog open={basics} onClose={() => { setBasics(false); onValidate() }} fullWidth maxWidth="sm" aria-labelledby="collection-basics-title">
      <DialogTitle id="collection-basics-title">Describe the items to extract</DialogTitle><DialogContent><Stack spacing={3} sx={{ pt: 1 }}>
        <TextField id="profile-name" label="Type of item" disabled={disabled} value={value.name} onChange={(event) => onChange({ ...value, name: event.target.value })} error={Boolean(issueText('name'))} helperText={issueText('name')} />
        <TextField id="profile-description" label="Additional guidance for this item type" helperText="Add a brief description to supplement your existing agent prompt. The AI uses both during extraction to decide what to include, exclude, and treat as a separate record." disabled={disabled} multiline minRows={2} value={value.description ?? ''} onChange={(event) => onChange({ ...value, description: event.target.value })} />
        {issues.filter((issue) => !issue.path.startsWith('fields')).map((issue, index) => <Alert severity="error" key={index}>{issue.message}</Alert>)}
      </Stack></DialogContent><DialogActions><Button onClick={() => { setBasics(false); onValidate() }}>Done</Button></DialogActions>
    </Dialog>
    <Dialog open={checks} onClose={() => setChecks(false)} fullWidth maxWidth="md" aria-labelledby="collection-checks-title"><DialogTitle id="collection-checks-title">Additional checks</DialogTitle><DialogContent><ProfileValidatorEditor value={value} onChange={onChange} onValidate={onValidate} issues={issues} disabled={disabled} /></DialogContent><DialogActions><Button onClick={() => setChecks(false)}>Done</Button></DialogActions></Dialog>
    <Dialog open={pendingRemoval !== null} onClose={() => setPendingRemoval(null)} aria-labelledby="remove-profile-field-title"><DialogTitle id="remove-profile-field-title">Remove this field from the draft?</DialogTitle><DialogContent>Its nested fields will also be removed. Saved revisions remain unchanged. Any checks referencing this field will need updating.</DialogContent><DialogActions><Button onClick={() => setPendingRemoval(null)}>Cancel</Button><Button color="error" disabled={disabled} onClick={() => { if (pendingRemoval) onChange(removeProfileField(value, pendingRemoval)); setPendingRemoval(null); setSelected(null) }}>Remove field</Button></DialogActions></Dialog>
    <Dialog open={pendingType !== null} onClose={() => setPendingType(null)} aria-labelledby="change-profile-kind-title"><DialogTitle id="change-profile-kind-title">Replace this answer format?</DialogTitle><DialogContent>Changing this format changes how the answer is stored and removes any parts or choices that the new format cannot represent. Its name and instructions stay. Cancel to keep the current format. Saved revisions are unchanged.</DialogContent><DialogActions><Button onClick={() => setPendingType(null)}>Cancel</Button><Button disabled={disabled} onClick={() => { if (pendingType) onChange(updateProfileField(value, pendingType.address, { value_schema: pendingType.schema })); setPendingType(null) }}>Change format</Button></DialogActions></Dialog>
  </Stack>
}

function answerSummary(schema: GenericProfileValueSchema): string {
  if (schema.kind === 'array') return `Multiple ${schema.items.kind === 'object' ? 'sets of details' : answerSummary(schema.items).toLowerCase() + ' answers'}`
  return ({ string: 'Text', integer: 'Whole number', number: 'Number', boolean: 'Yes or no', enum: 'One of your choices', object: 'Related details' })[schema.kind]
}
function answerExample(schema: GenericProfileValueSchema): string {
  if (schema.kind === 'array') return answerExample(schema.items)
  if (schema.kind === 'object') return schema.fields.map((field) => field.display_name || field.key).join(' + ')
  if (schema.kind === 'enum') return schema.values.map((choice) => choice.replaceAll('_', ' ')).join(' / ')
  return ({ string: 'Words or labels from the paper', integer: 'For example, 3', number: 'For example, 3.5', boolean: 'Yes / No' })[schema.kind]
}
