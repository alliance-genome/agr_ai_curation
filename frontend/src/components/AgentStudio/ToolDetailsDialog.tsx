/**
 * ToolDetailsDialog Component
 *
 * Dialog that displays detailed information about a tool.
 * For multi-method tools (like agr_curation_query), shows which methods
 * are used by the current agent and their specific parameters.
 */

import { useState, useEffect } from 'react'
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Box,
  Typography,
  Chip,
  CircularProgress,
  Alert,
  Divider,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Tooltip,
} from '@mui/material'
import BuildIcon from '@mui/icons-material/Build'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import CodeIcon from '@mui/icons-material/Code'
import CategoryIcon from '@mui/icons-material/Category'
import InfoIcon from '@mui/icons-material/Info'

import { fetchToolDetails } from '@/services/agentStudioService'
import type { ToolInfo, ToolMethod, ToolParameter } from '@/types/promptExplorer'

interface ToolDetailsDialogProps {
  open: boolean
  onClose: () => void
  toolId: string | null
  agentId?: string | null
  agentName?: string | null
}

// Category to color mapping
const CATEGORY_COLORS: Record<string, 'primary' | 'secondary' | 'success' | 'warning' | 'info'> = {
  Database: 'primary',
  Document: 'info',
  'PDF Extraction': 'info',
  Export: 'success',
  Output: 'success',
  Ontology: 'secondary',
  Chemical: 'warning',
  Routing: 'secondary',
}

function ToolDetailsDialog({
  open,
  onClose,
  toolId,
  agentId,
  agentName,
}: ToolDetailsDialogProps) {
  const [tool, setTool] = useState<ToolInfo | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open && toolId) {
      setLoading(true)
      setError(null)
      setTool(null)

      fetchToolDetails(toolId, agentId || undefined)
        .then((data) => {
          setTool(data)
        })
        .catch((err) => {
          setError(err instanceof Error ? err.message : 'Failed to load tool details')
        })
        .finally(() => {
          setLoading(false)
        })
    }
  }, [open, toolId, agentId])

  const handleClose = () => {
    onClose()
  }

  // Determine which methods to show
  const methodsToShow = tool?.relevant_methods || tool?.methods

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="md" fullWidth>
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <BuildIcon color="primary" />
        Tool Details
      </DialogTitle>

      <DialogContent>
        {loading && (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
            <CircularProgress />
          </Box>
        )}

        {error && (
          <Alert severity="error" sx={{ mt: 1 }}>
            {error}
          </Alert>
        )}

        {tool && (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, mt: 1 }}>
            {/* Header with name and category */}
            <Box sx={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
              <Box>
                <Typography variant="h6" sx={{ fontWeight: 600 }}>
                  {tool.name}
                </Typography>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ fontFamily: 'monospace', fontSize: '0.8rem' }}
                >
                  {toolId}
                </Typography>
              </Box>
              <Chip
                icon={<CategoryIcon />}
                label={tool.category}
                size="small"
                color={CATEGORY_COLORS[tool.category] || 'default'}
                variant="outlined"
              />
            </Box>

            {/* Parent tool reference for method-level tools */}
            {tool.parent_tool && (
              <Alert severity="info" icon={<InfoIcon />} sx={{ py: 0.5 }}>
                <Typography variant="body2">
                  This is a method of the <strong>{tool.parent_tool}</strong> tool.
                </Typography>
              </Alert>
            )}

            {/* Agent context alert (for parent tools only) */}
            {!tool.parent_tool && tool.agent_context && agentName && (
              <Alert severity="info" icon={<InfoIcon />} sx={{ py: 0.5 }}>
                <Typography variant="body2">
                  <strong>{agentName}</strong> uses {tool.agent_context.methods.length} of this tool&apos;s methods:
                </Typography>
                <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', mt: 0.5 }}>
                  {tool.agent_context.methods.map((method) => (
                    <Chip
                      key={method}
                      label={method}
                      size="small"
                      variant="outlined"
                      sx={{ fontFamily: 'monospace', fontSize: '0.75rem' }}
                    />
                  ))}
                </Box>
              </Alert>
            )}

            {/* Description */}
            <Box>
              <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                Description
              </Typography>
              <Typography variant="body2">
                {tool.description}
              </Typography>
            </Box>

            {/* Documentation summary */}
            {tool.documentation?.summary && (
              <Box>
                <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                  How It Works
                </Typography>
                <Typography variant="body2">
                  {tool.documentation.summary}
                </Typography>
              </Box>
            )}

            <Divider />

            {/* Parameters (for simple tools and method-level tools) */}
            {tool.documentation?.parameters && tool.documentation.parameters.length > 0 && !methodsToShow && (
              <Box>
                <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                  Parameters
                </Typography>
                <ParameterTable parameters={tool.documentation.parameters} />
              </Box>
            )}

            {/* Example (for method-level tools) */}
            {tool.example && Object.keys(tool.example).length > 0 && (
              <Box>
                <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                  Example Usage
                </Typography>
                <Box
                  component="pre"
                  sx={{
                    p: 1.5,
                    backgroundColor: 'action.hover',
                    borderRadius: 1,
                    fontSize: '0.8rem',
                    fontFamily: 'monospace',
                    overflow: 'auto',
                    maxHeight: 150,
                  }}
                >
                  {JSON.stringify(tool.example, null, 2)}
                </Box>
              </Box>
            )}

            {/* Methods (for multi-method tools) */}
            {methodsToShow && Object.keys(methodsToShow).length > 0 && (
              <Box>
                <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                  {tool.agent_context ? 'Methods Used by This Agent' : 'Available Methods'}
                </Typography>
                <Box sx={{ mt: 1 }}>
                  {Object.entries(methodsToShow).map(([methodName, method]) => (
                    <MethodAccordion
                      key={methodName}
                      methodName={methodName}
                      method={method}
                    />
                  ))}
                </Box>
              </Box>
            )}

            {/* Source file */}
            <Box sx={{ mt: 1 }}>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                <CodeIcon sx={{ fontSize: 14 }} />
                Source: {tool.source_file}
              </Typography>
            </Box>
          </Box>
        )}
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={handleClose}>
          Close
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// Parameter table component
function ParameterTable({ parameters }: { parameters: ToolParameter[] }) {
  return (
    <TableContainer component={Paper} variant="outlined" sx={{ mt: 1 }}>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Name</TableCell>
            <TableCell>Type</TableCell>
            <TableCell>Required</TableCell>
            <TableCell>Description</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {parameters.map((param) => (
            <TableRow key={param.name}>
              <TableCell>
                <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
                  {param.name}
                </Typography>
              </TableCell>
              <TableCell>
                <Chip label={param.type} size="small" variant="outlined" />
              </TableCell>
              <TableCell>
                {param.required ? (
                  <Chip label="Required" size="small" color="error" variant="filled" />
                ) : (
                  <Chip label="Optional" size="small" variant="outlined" />
                )}
              </TableCell>
              <TableCell>
                <Typography variant="body2" color="text.secondary">
                  {param.description}
                </Typography>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  )
}

// Method accordion component
function MethodAccordion({ methodName, method }: { methodName: string; method: ToolMethod }) {
  return (
    <Accordion
      disableGutters
      elevation={0}
      sx={{
        border: '1px solid',
        borderColor: 'divider',
        '&:not(:last-child)': { borderBottom: 0 },
        '&:before': { display: 'none' },
      }}
    >
      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, width: '100%' }}>
          <Typography
            variant="body2"
            sx={{ fontFamily: 'monospace', fontWeight: 600 }}
          >
            {methodName}
          </Typography>
          {method.required_params.length > 0 && (
            <Tooltip title="Required parameters">
              <Chip
                label={`${method.required_params.length} required`}
                size="small"
                color="error"
                variant="outlined"
                sx={{ fontSize: '0.7rem', height: 20 }}
              />
            </Tooltip>
          )}
        </Box>
      </AccordionSummary>
      <AccordionDetails>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
          <Typography variant="body2" color="text.secondary">
            {method.description}
          </Typography>

          {/* Required params */}
          {method.required_params.length > 0 && (
            <Box>
              <Typography variant="caption" color="error.main" fontWeight={500}>
                Required:
              </Typography>
              <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', mt: 0.5 }}>
                {method.required_params.map((param) => (
                  <Chip
                    key={param}
                    label={param}
                    size="small"
                    color="error"
                    variant="outlined"
                    sx={{ fontFamily: 'monospace', fontSize: '0.75rem' }}
                  />
                ))}
              </Box>
            </Box>
          )}

          {/* Optional params */}
          {method.optional_params.length > 0 && (
            <Box>
              <Typography variant="caption" color="text.secondary" fontWeight={500}>
                Optional:
              </Typography>
              <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', mt: 0.5 }}>
                {method.optional_params.map((param) => (
                  <Chip
                    key={param}
                    label={param}
                    size="small"
                    variant="outlined"
                    sx={{ fontFamily: 'monospace', fontSize: '0.75rem' }}
                  />
                ))}
              </Box>
            </Box>
          )}

          {/* Example */}
          {method.example && Object.keys(method.example).length > 0 && (
            <Box>
              <Typography variant="caption" color="text.secondary" fontWeight={500}>
                Example:
              </Typography>
              <Box
                component="pre"
                sx={{
                  mt: 0.5,
                  p: 1,
                  backgroundColor: 'action.hover',
                  borderRadius: 1,
                  fontSize: '0.75rem',
                  fontFamily: 'monospace',
                  overflow: 'auto',
                  maxHeight: 120,
                }}
              >
                {JSON.stringify(method.example, null, 2)}
              </Box>
            </Box>
          )}
        </Box>
      </AccordionDetails>
    </Accordion>
  )
}

export default ToolDetailsDialog
