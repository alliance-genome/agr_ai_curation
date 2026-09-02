/**
 * AgentToolsTable
 *
 * Tools as a count plus a "Show all tools" disclosure. Closed, it previews
 * the first three tool names as chips and a +N remainder. Open, it renders a
 * table with the tool name, a one-line purpose, and a Details link that
 * opens the tool details slide-over. No chip wall at any count.
 */

import { useId, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Chip,
  Link,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import ChevronRightIcon from '@mui/icons-material/ChevronRight'

import { CountPill, MONO_FONT_FAMILY, SectionHeading, tableHeadCellSx as headCellSx } from './agentGuidePrimitives'

const PREVIEW_COUNT = 3

interface AgentToolsTableProps {
  tools: string[]
  /** One-line purpose per tool id. Missing entries render an honest placeholder. */
  descriptions: Record<string, string>
  /** Set when the tool inventory request failed; purposes are then unknown, not missing. */
  inventoryError?: string | null
  onRetryInventory?: () => void
  onShowDetails: (toolId: string) => void
}

function InventoryErrorAlert({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <Alert
      severity="error"
      variant="outlined"
      sx={{ py: 0.25, '& .MuiAlert-message': { fontSize: 12.5 } }}
      action={onRetry ? (
        <Button color="inherit" size="small" onClick={onRetry} sx={{ textTransform: 'none' }}>
          Retry
        </Button>
      ) : undefined}
    >
      Tool descriptions could not be loaded. {message}
    </Alert>
  )
}


function AgentToolsTable({ tools, descriptions, inventoryError, onRetryInventory, onShowDetails }: AgentToolsTableProps) {
  const [open, setOpen] = useState(false)
  const tableId = useId()
  const count = tools.length
  const countLabel = `${count} ${count === 1 ? 'tool' : 'tools'}`
  const errorAlert = inventoryError ? <InventoryErrorAlert message={inventoryError} onRetry={onRetryInventory} /> : null

  if (count === 0) {
    return (
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75 }}>
        <SectionHeading>Tools</SectionHeading>
        <Typography variant="body2" color="text.secondary">
          This agent has no tools. It works from the paper text and its prompt alone.
        </Typography>
      </Box>
    )
  }

  const toggle = (
    <Button
      size="small"
      onClick={() => setOpen((value) => !value)}
      aria-expanded={open}
      aria-controls={tableId}
      startIcon={open ? <ExpandMoreIcon /> : <ChevronRightIcon />}
      sx={{ textTransform: 'none', fontWeight: 500, fontSize: 13, px: 0.75, minWidth: 0 }}
    >
      {open ? 'Hide tools' : 'Show all tools'}
    </Button>
  )

  if (!open) {
    return (
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75 }}>
        <SectionHeading>Tools</SectionHeading>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', fontSize: 13 }}>
          <Box component="span" sx={{ fontWeight: 500 }}>{countLabel}</Box>
          {tools.slice(0, PREVIEW_COUNT).map((tool) => (
            <Chip
              key={tool}
              label={tool}
              size="small"
              variant="outlined"
              sx={{ height: 22, fontSize: 12, fontFamily: MONO_FONT_FAMILY, maxWidth: 240 }}
            />
          ))}
          {count > PREVIEW_COUNT && (
            <Box component="span" sx={{ fontSize: 12, color: 'text.secondary' }}>+{count - PREVIEW_COUNT}</Box>
          )}
          {toggle}
        </Box>
        {errorAlert}
      </Box>
    )
  }

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75 }}>
      <SectionHeading action={toggle}>
        Tools <CountPill label={countLabel}>{count}</CountPill>
      </SectionHeading>
      {errorAlert}
      <TableContainer id={tableId} sx={{ border: 1, borderColor: 'divider', borderRadius: 2 }}>
        <Table size="small" aria-label="Tools" sx={{ tableLayout: 'fixed' }}>
          <TableHead>
            <TableRow>
              <TableCell sx={{ ...headCellSx, width: '36%' }}>Tool</TableCell>
              <TableCell sx={headCellSx}>Purpose</TableCell>
              <TableCell sx={{ ...headCellSx, width: 88 }} aria-label="Actions" />
            </TableRow>
          </TableHead>
          <TableBody>
            {tools.map((tool) => {
              const purpose = descriptions[tool]
              const purposeText = purpose || (inventoryError ? 'Not loaded' : 'No description yet')
              return (
                <TableRow key={tool}>
                  <TableCell sx={{ fontSize: 13, py: 0.75, minWidth: 0 }}>
                    <Box
                      component="code"
                      title={tool}
                      sx={{
                        fontFamily: MONO_FONT_FAMILY,
                        fontWeight: 500,
                        fontSize: 12.5,
                        display: 'block',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {tool}
                    </Box>
                  </TableCell>
                  <TableCell sx={{ fontSize: 13, py: 0.75, color: purpose ? 'text.secondary' : 'text.disabled' }}>
                    {purposeText}
                  </TableCell>
                  <TableCell sx={{ py: 0.75, textAlign: 'right' }}>
                    <Link
                      component="button"
                      type="button"
                      underline="hover"
                      onClick={() => onShowDetails(tool)}
                      aria-label={`Details for ${tool}`}
                      sx={{ fontSize: 12, fontWeight: 500 }}
                    >
                      Details
                    </Link>
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </TableContainer>
    </Box>
  )
}

export default AgentToolsTable
