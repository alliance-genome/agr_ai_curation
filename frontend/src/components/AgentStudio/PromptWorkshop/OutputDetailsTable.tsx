import { Button, IconButton, Stack, Table, TableHead, TableBody, TableRow, TableCell, TableContainer, Typography } from '@mui/material'
import InfoOutlined from '@mui/icons-material/InfoOutlined'
import type { GenericProfileContract, ProfileMappingDiagnostic } from '@/services/genericProfileService'
import ValidatorAttachmentStatus, { ValidatorAttachmentHeading } from './ValidatorAttachmentStatus'
import { profileFieldRows, type ProfileFieldAddress } from './profileEditorModel'
import { answerSummary, answerExample } from './outputAnswerLabels'

export default function OutputDetailsTable({ value, issues = [], disabled = false, onEdit, onInfo }: {
  value: GenericProfileContract; issues?: ProfileMappingDiagnostic[]; disabled?: boolean
  onEdit?: (address: ProfileFieldAddress) => void
  onInfo?: (anchor: HTMLElement, title: string, text: string) => void
}) {
  const rows = profileFieldRows(value)
  return (
      <TableContainer sx={{ position: 'relative', border: 1, borderColor: 'divider', borderRadius: 1 }} tabIndex={0} role="region" aria-label="Details table; scroll horizontally on small screens">
      <Table aria-label="Details to collect" sx={{ minWidth: 560 }}>
        <TableHead sx={{ bgcolor: 'action.hover' }}><TableRow><TableCell sx={{ fontWeight: 700 }}>Detail</TableCell><TableCell sx={{ fontWeight: 700 }}>What to collect</TableCell><TableCell sx={{ fontWeight: 700 }}>Include</TableCell><TableCell><ValidatorAttachmentHeading /></TableCell>{onEdit && <TableCell>Actions</TableCell>}</TableRow></TableHead>
        <TableBody>
        {rows.map((entry) => {
          const name = entry.field.display_name || entry.field.key
          const parent = rows.find((candidate) => candidate.address.join('.') === entry.address.slice(0, -1).join('.'))
          return <TableRow key={entry.schemaPath} sx={{ bgcolor: entry.depth ? 'action.hover' : undefined }}>
            <TableCell component="th" scope="row" sx={{ pl: entry.depth ? 4 : 2 }}><Stack direction="row" sx={{  alignItems: "center", gap: 0.5 }}>
              <Typography sx={{  fontWeight: 600 }}>{name}</Typography>
              {onInfo && <IconButton aria-label={`About ${name}`} onClick={(event) => onInfo?.(event.currentTarget, name, entry.field.description || 'No extraction instructions have been added for this field.')}><InfoOutlined fontSize="small" /></IconButton>}
            </Stack>{parent && <Typography variant="body2" color="text.secondary">Part of {parent.field.display_name || parent.field.key}</Typography>}</TableCell>
            <TableCell><Typography>{answerSummary(entry.field.value_schema)}</Typography><Typography variant="body2" color="text.secondary">{answerExample(entry.field.value_schema)}</Typography></TableCell>
            <TableCell><Typography color="text.secondary">{entry.field.required ? (entry.depth ? 'With its parent answer' : 'Every record') : 'When available'}</Typography></TableCell>
            <TableCell><ValidatorAttachmentStatus value={value} address={entry.address} issues={issues} onEdit={onEdit ? () => onEdit(entry.address) : undefined} /></TableCell>{onEdit && <TableCell><Button disabled={disabled} aria-label={`Edit ${name}`} onClick={() => onEdit(entry.address)}>Edit</Button></TableCell>}
          </TableRow>
        })}
        {rows.length === 0 && <TableRow><TableCell colSpan={onEdit ? 5 : 4} sx={{ py: 5, textAlign: 'center' }}><Typography sx={{  fontWeight: 600 }}>No details yet</Typography><Typography color="text.secondary" sx={{ mt: 1 }}>Add your first detail, such as “Stock name”.</Typography></TableCell></TableRow>}
        </TableBody>
      </Table></TableContainer>
  )
}
