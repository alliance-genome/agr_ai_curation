import ProfileValidatorCatalog from './ProfileValidatorCatalog'
import ValidatorAttachmentStatus, { ValidatorAttachmentHeading } from './ValidatorAttachmentStatus'
import { useState } from 'react'
import { Alert, Box, Button, Stack, Step, StepLabel, Stepper, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Typography } from '@mui/material'
import OutputStructureEditor, { type OutputStructureEditorProps } from './OutputStructureEditor'
import { profileFieldRows } from './profileEditorModel'

/** One shared walkthrough for the real Workshop and the standalone design preview. */
export default function OutputStructureWorkflow(props: OutputStructureEditorProps) {
  const { value, disabled, validating, issues, onValidate } = props
  const [stage, setStage] = useState<'edit' | 'review' | 'done'>('edit')
  const rows = profileFieldRows(value)
  return <ProfileValidatorCatalog value={value}><Stack spacing={3}>
    {stage !== 'done' && <Stepper activeStep={stage === 'review' ? 2 : value.name ? 1 : 0} alternativeLabel>
      {['Name the item type', 'Choose its details', 'Review & finish'].map((label) => <Step key={label}><StepLabel>{label}</StepLabel></Step>)}
    </Stepper>}
    {stage === 'edit' && <>
      <OutputStructureEditor {...props} />
      {value.name && <Button variant="contained" disabled={disabled || validating} sx={{ alignSelf: 'flex-end' }} onClick={() => { onValidate(); setStage('review') }}>Review {value.name}</Button>}
    </>}
    {stage === 'review' && <Stack spacing={3}>
      <Box><Typography component="h2" variant="h5">Review {value.name}</Typography>
        <Typography sx={{ mt: 1 }}>{value.description || 'Create a separate record for each item of this type found in a paper.'}</Typography></Box>
      <TableContainer sx={{ position: 'relative', border: 1, borderColor: 'divider', borderRadius: 1 }} tabIndex={0} role="region" aria-label="Review table; scroll horizontally on small screens">
        <Table aria-label="Extraction plan" sx={{ minWidth: 440 }}>
          <TableHead sx={{ bgcolor: 'action.hover' }}><TableRow><TableCell>Detail or part</TableCell><TableCell>Always include</TableCell><TableCell>Empty answer allowed</TableCell><TableCell><ValidatorAttachmentHeading /></TableCell></TableRow></TableHead>
          <TableBody>{rows.map((row) => <TableRow key={row.schemaPath} sx={{ bgcolor: row.depth ? 'action.hover' : undefined }}>
            <TableCell component="th" scope="row" sx={{ pl: row.depth ? 4 : 2 }}>
              <Typography fontWeight={600}>{row.field.display_name || row.field.key}</Typography>
              {row.depth > 0 && <Typography variant="body2" color="text.secondary">Part of {rows.find((parent) => parent.address.join('.') === row.address.slice(0, -1).join('.'))?.field.display_name || 'parent answer'}</Typography>}
              {row.field.description && <Typography variant="body2" sx={{ mt: 0.5 }}>{row.field.description}</Typography>}
            </TableCell>
            <TableCell>{row.field.required ? (row.depth ? 'With its parent answer' : 'Yes') : 'No'}</TableCell>
            <TableCell>{row.field.nullable ? 'Yes' : 'No'}</TableCell><TableCell><ValidatorAttachmentStatus value={value} address={row.address} issues={issues} /></TableCell>
          </TableRow>)}
          {rows.length === 0 && <TableRow><TableCell colSpan={4} sx={{ py: 4, textAlign: 'center' }}>No details yet. Go back to add the information you want to collect.</TableCell></TableRow>}
          </TableBody>
        </Table>
      </TableContainer>
      {validating && <Typography role="status">Validating your fields…</Typography>}
      {issues.length > 0 && <Alert severity="error">Some settings need attention. Go back to details to review the highlighted fields.</Alert>}
      <Typography color="text.secondary">Finishing this walkthrough keeps your plan in the draft. Use Workshop Save when you are ready to save the agent.</Typography>
      <Stack direction="row" gap={2} flexWrap="wrap"><Button onClick={() => setStage('edit')}>Back to details</Button><Button variant="contained" disabled={disabled || validating || issues.length > 0} onClick={() => setStage('done')}>Finish {value.name}</Button></Stack>
    </Stack>}
    {stage === 'done' && <Stack spacing={3}>
      <Typography component="h2" variant="h5">Your extraction plan</Typography>
      <Stack direction="row" justifyContent="space-between" alignItems="center" gap={2} flexWrap="wrap" sx={{ py: 3, borderTop: 1, borderBottom: 1, borderColor: 'divider' }}>
        <Box><Typography component="h3" variant="h6">{value.name}</Typography><Typography color="text.secondary">{value.fields.map((field) => field.display_name || field.key).join(' · ') || 'No details added'}</Typography></Box>
        <Button onClick={() => setStage('edit')}>Edit {value.name}</Button>
      </Stack>
      <Typography color="text.secondary">You can reopen this plan to make changes. Use Workshop Save to save your agent.</Typography>
    </Stack>}
  </Stack></ProfileValidatorCatalog>
}
