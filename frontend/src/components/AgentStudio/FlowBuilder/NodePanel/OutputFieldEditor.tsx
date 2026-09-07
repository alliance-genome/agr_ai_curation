import { Fragment, useEffect, useRef, useState } from 'react'
import { Alert, Box, Button, Checkbox, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, IconButton, Radio, RadioGroup, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from '@mui/material'
import ArrowUpwardIcon from '@mui/icons-material/ArrowUpward'
import ArrowDownwardIcon from '@mui/icons-material/ArrowDownward'
import { validateFlowDraft } from '@/services/agentStudioService'
import type { FlowDraftValidationResponse } from '@/services/agentStudioService'
import type { FlowDefinition } from '../types'
import type { OutputBindingView } from '../types'

type Catalog = NonNullable<FlowDraftValidationResponse['projection_fields_by_node']>
interface Column { key: string; header: string; field_ref: string; source_node_id: string }
interface Props {
  format: 'csv' | 'tsv' | 'json'
  definition: FlowDefinition
  binding?: OutputBindingView
  value: Record<string, unknown> | null
  onChange: (value: Record<string, unknown> | null) => void
}

export default function OutputFieldEditor({ format, definition, binding, value, onChange }: Props) {
  const request = useRef(0)
  useEffect(() => () => { request.current += 1 }, [])
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [catalog, setCatalog] = useState<Catalog>({})
  const [columns, setColumns] = useState<Column[]>([])
  const [search, setSearch] = useState('')
  const [base, setBase] = useState('')
  const selected = value?.selection_mode === 'selected_fields'
  const sources = binding?.status === 'bound' ? binding.sources : []
  const stale = open && base !== JSON.stringify(definition)
  const headers = columns.map((c) => c.header.trim())
  const invalid = headers.some((h) => !h) || new Set(headers).size !== headers.length
  const unavailable = columns.some((c) => !catalog[c.source_node_id]?.schema_fingerprint || !catalog[c.source_node_id]?.fields.some((f) => f.ref === c.field_ref))

  function close() { request.current += 1; setOpen(false) }
  async function edit() {
    const generation = ++request.current
    setOpen(true); setLoading(true); setError(''); setSearch('')
    setBase(JSON.stringify(definition))
    setColumns(selected && Array.isArray(value.columns) ? value.columns.map((column) => ({
      key: typeof column?.key === 'string' ? column.key : '',
      header: typeof column?.header === 'string' ? column.header : typeof column?.key === 'string' ? column.key : '',
      field_ref: typeof column?.field_ref === 'string' ? column.field_ref : '',
      source_node_id: typeof column?.source_node_id === 'string' ? column.source_node_id : '',
    })) : [])
    try {
      const result = await validateFlowDraft(definition, 'pre_apply', '', '')
      if (generation === request.current) setCatalog(result.projection_fields_by_node || {})
    } catch {
      if (generation === request.current) setError('The source fields could not be loaded. Close this window and try again.')
    } finally { if (generation === request.current) setLoading(false) }
  }
  function toggle(source: string, ref: string, label: string) {
    setColumns((current) => current.some((c) => c.source_node_id === source && c.field_ref === ref)
      ? current.filter((c) => c.source_node_id !== source || c.field_ref !== ref)
      : [...current, { key: `${source}:${ref}`, header: label, field_ref: ref, source_node_id: source }])
  }
  function move(index: number, delta: number) {
    setColumns((current) => { const next = [...current]; [next[index], next[index + delta]] = [next[index + delta], next[index]]; return next })
  }
  function done() {
    const ids = [...new Set(columns.map((c) => c.source_node_id))]
    onChange({ format, selection_mode: 'selected_fields', row_source: 'object', row_strategy: 'wide_union',
      columns: columns.map((c) => ({ ...c, key: c.header.trim(), header: c.header.trim() })),
      selected_sources: ids.map((id) => ({ node_id: id, schema_fingerprint: catalog[id].schema_fingerprint })),
      json_shape: 'rows', missing_value: format === 'json' ? null : '',
    })
    close()
  }
  return <Box component="section" sx={{ display: 'grid', gap: 1 }}>
    <Typography fontWeight={600}>Choose how to build the file</Typography>
    <RadioGroup value={selected ? 'selected_fields' : 'guided'} aria-label="File content mode"
      onChange={(_, mode) => { if (mode === 'selected_fields') void edit(); else onChange(null) }}>
      <FormControlLabel value="selected_fields" control={<Radio />} label="Use selected fields" />
      <FormControlLabel value="guided" control={<Radio />} label="Let AI arrange the output" />
    </RadioGroup>
    <Typography variant="body2">{selected
      ? `${Array.isArray(value.columns) ? value.columns.length : 0} fields selected. These columns are fixed and values are copied from your results.`
      : 'Describe the layout in Output instructions. AI chooses columns from the available results; the layout may vary between runs.'}</Typography>
    <Button variant={selected ? 'outlined' : 'text'} onClick={() => void edit()} disabled={sources.length === 0}>Choose output fields</Button>
    <Dialog open={open} maxWidth="lg" fullWidth aria-labelledby="output-fields-title" disableEscapeKeyDown>
      <DialogTitle id="output-fields-title">Choose output fields · {format.toUpperCase()}</DialogTitle>
      <DialogContent dividers sx={{ display: 'grid', gap: 2 }}>
        <Typography>Choose what to include from the connected steps. Each item keeps its own row; records from different steps are never joined.</Typography>
        <Typography variant="body2">{format === 'json'
          ? 'Answers with parts stay as objects, and lists stay as arrays. Missing answers are null.'
          : 'Answers with parts and lists stay together as JSON inside a cell. Choose individual parts for separate columns. Missing answers are blank.'} Selecting a column does not make the extractor require an answer.</Typography>
        {loading && <Box role="status"><CircularProgress size={20} /> Loading saved source fields…</Box>}
        {error && <Alert severity="error">{error}</Alert>}
        {stale && <Alert severity="warning">The flow changed while this window was open. Cancel and reopen to review its current sources.</Alert>}
        {!loading && !error && <>
          <TextField size="small" label="Find a field" value={search} onChange={(e) => setSearch(e.target.value)} />
          <Box sx={{ maxHeight: '38vh', overflow: 'auto' }}><Table stickyHeader size="small" aria-label="Available output fields" sx={{ border: 1, borderColor: 'divider' }}>
            <TableHead sx={{ bgcolor: 'action.hover' }}><TableRow><TableCell>Include</TableCell><TableCell>Detail</TableCell><TableCell>Answer format</TableCell></TableRow></TableHead>
            <TableBody>{sources.map((source) => {
              const fields = catalog[source.sourceNodeId]?.fields || []
              return <Fragment key={source.sourceNodeId}>
                <TableRow key={`${source.sourceNodeId}-label`} sx={{ bgcolor: 'action.selected' }}><TableCell colSpan={3}><strong>{source.sourceLabel}</strong></TableCell></TableRow>
                {fields.length === 0 && <TableRow key={`${source.sourceNodeId}-empty`}><TableCell colSpan={3}>No declared fields available. Save a custom output structure or use a packaged extractor first.</TableCell></TableRow>}
                {fields.filter((f) => `${f.label} ${f.group || ''}`.toLowerCase().includes(search.toLowerCase())).map((field) => <TableRow key={`${source.sourceNodeId}:${field.ref}`}>
                  <TableCell><Checkbox checked={columns.some((c) => c.source_node_id === source.sourceNodeId && c.field_ref === field.ref)}
                    inputProps={{ 'aria-label': `Include ${source.sourceLabel}: ${field.label}` }} onChange={() => toggle(source.sourceNodeId, field.ref, sources.length > 1 ? `${source.sourceLabel} — ${field.label}` : field.label)} /></TableCell>
                  <TableCell>{field.group && <Typography variant="caption" display="block">{field.group}</Typography>}{field.label}</TableCell>
                  <TableCell>{({ string: 'Text', integer: 'Whole number', number: 'Decimal number', boolean: 'Yes or no', object: 'Answer with parts', list: 'List', array: 'List', enum: 'Choice', float: 'Decimal number', curie: 'Identifier', identifier: 'Identifier', object_ref: 'Reference to an item', field_ref: 'Reference to a detail' } as Record<string,string>)[field.value_type] || 'Value'}</TableCell>
                </TableRow>)}
              </Fragment>
            })}</TableBody>
          </Table></Box>
          <Typography fontWeight={600}>Your output · {columns.length} fields</Typography>
          <Typography variant="body2">Preview of the layout, not paper data. Every selected column appears, even when no items are found.</Typography>
          {invalid && <Alert severity="error">Give each output column a different, nonempty name.</Alert>}
          {unavailable && <Alert severity="error">Some saved fields are no longer available. Remove them below and choose from the current source fields.</Alert>}
          <Table size="small" aria-label="Selected output fields" sx={{ border: 1, borderColor: 'divider' }}>
            <TableHead sx={{ bgcolor: 'action.hover' }}><TableRow><TableCell>Column name</TableCell><TableCell>Source detail</TableCell><TableCell>Order</TableCell><TableCell>Remove</TableCell></TableRow></TableHead>
            <TableBody>{columns.length === 0 ? <TableRow><TableCell colSpan={4}>Select fields above to build your output.</TableCell></TableRow> : columns.map((column, index) => <TableRow key={`${column.source_node_id}:${column.field_ref}`}>
              <TableCell><TextField size="small" value={column.header} inputProps={{ 'aria-label': `Column ${index + 1} name` }} onChange={(e) => setColumns((current) => current.map((c, i) => i === index ? { ...c, header: e.target.value } : c))} /></TableCell>
              <TableCell>{sources.find((s) => s.sourceNodeId === column.source_node_id)?.sourceLabel} · {catalog[column.source_node_id]?.fields.find((f) => f.ref === column.field_ref)?.label || 'Unavailable field'}</TableCell>
              <TableCell><IconButton aria-label={`Move column ${index + 1} up`} disabled={index === 0} onClick={() => move(index, -1)}><ArrowUpwardIcon /></IconButton><IconButton aria-label={`Move column ${index + 1} down`} disabled={index === columns.length - 1} onClick={() => move(index, 1)}><ArrowDownwardIcon /></IconButton></TableCell>
              <TableCell><Button onClick={() => setColumns((current) => current.filter((_, i) => i !== index))}>Remove</Button></TableCell>
            </TableRow>)}</TableBody>
          </Table>
        </>}
      </DialogContent>
      <DialogActions><Button onClick={close}>Cancel</Button><Button variant="contained" onClick={done} disabled={loading || !!error || stale || invalid || unavailable || columns.length === 0}>Use these fields</Button></DialogActions>
    </Dialog>
  </Box>
}
