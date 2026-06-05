import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) {
    return ''
  }
  if (
    typeof value === 'string' ||
    typeof value === 'number' ||
    typeof value === 'boolean'
  ) {
    return String(value)
  }
  return JSON.stringify(value)
}

export default function SubTableFieldValue({ value }: { value: unknown }) {
  const rows = Array.isArray(value)
    ? value.filter(isRecord)
    : isRecord(value)
      ? [value]
      : []

  if (rows.length === 0) {
    return null
  }

  const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))]

  return (
    <Accordion disableGutters sx={{ backgroundColor: 'transparent', boxShadow: 'none' }}>
      <AccordionSummary expandIcon={<ExpandMoreIcon fontSize="small" />}>
        <Typography sx={{ fontWeight: 700 }} variant="body2">
          {rows.length} {rows.length === 1 ? 'item' : 'items'}
        </Typography>
      </AccordionSummary>
      <AccordionDetails sx={{ overflowX: 'auto', pt: 0 }}>
        <Box sx={{ minWidth: 360 }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                {columns.map((column) => (
                  <TableCell key={column}>{column}</TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((row, rowIndex) => (
                <TableRow key={rowIndex}>
                  {columns.map((column) => (
                    <TableCell key={column}>{formatValue(row[column])}</TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Box>
      </AccordionDetails>
    </Accordion>
  )
}
