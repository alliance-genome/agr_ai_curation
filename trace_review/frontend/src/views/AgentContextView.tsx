import { useState } from 'react';
import { Box, Typography, Paper, Chip, Card, CardContent, Grid, Accordion, AccordionSummary, AccordionDetails, Table, TableBody, TableCell, TableContainer, TableHead, TableRow } from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import SmartToyIcon from '@mui/icons-material/SmartToy';
import BuildIcon from '@mui/icons-material/Build';
import { AgentContextData, AgentConfig } from '../types';

interface AgentContextViewProps {
  data: AgentContextData;
}

function AgentCard({ agent, title }: { agent: AgentConfig; title: string }) {
  const [showInstructions, setShowInstructions] = useState(false);

  return (
    <Card sx={{ mb: 2 }}>
      <CardContent>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 2 }}>
          <SmartToyIcon color="primary" />
          <Typography variant="h6">{title}</Typography>
          <Chip label={agent.agent_type} color="primary" size="small" />
        </Box>

        <Grid container spacing={2}>
          <Grid item xs={6} md={3}>
            <Typography color="text.secondary" variant="body2">Model</Typography>
            <Chip label={agent.model} size="small" variant="outlined" color="secondary" />
          </Grid>
          <Grid item xs={6} md={3}>
            <Typography color="text.secondary" variant="body2">Temperature</Typography>
            <Typography variant="body1">{agent.temperature ?? 'N/A'}</Typography>
          </Grid>
          <Grid item xs={6} md={3}>
            <Typography color="text.secondary" variant="body2">Tool Choice</Typography>
            <Typography variant="body1">{agent.tool_choice ?? 'auto'}</Typography>
          </Grid>
          <Grid item xs={6} md={3}>
            <Typography color="text.secondary" variant="body2">Generations</Typography>
            <Chip label={agent.generation_count} color="info" size="small" />
          </Grid>
        </Grid>

        {/* Tools Available */}
        {agent.tools_available.length > 0 && (
          <Box sx={{ mt: 2 }}>
            <Typography color="text.secondary" variant="body2" gutterBottom>
              Tools Available ({agent.tools_available.length})
            </Typography>
            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
              {agent.tools_available.map((tool) => (
                <Chip key={tool} label={tool} size="small" variant="outlined" />
              ))}
            </Box>
          </Box>
        )}

        {/* Instructions */}
        {(agent.full_instructions || agent.instructions_preview) && (
          <Accordion
            expanded={showInstructions}
            onChange={() => setShowInstructions(!showInstructions)}
            sx={{ mt: 2, backgroundColor: 'rgba(255,255,255,0.05)' }}
          >
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>
              <Typography variant="body2">
                Instructions ({agent.instructions_length.toLocaleString()} chars)
              </Typography>
            </AccordionSummary>
            <AccordionDetails>
              <Box
                sx={{
                  fontFamily: 'monospace',
                  fontSize: '0.8rem',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  maxHeight: 500,
                  overflow: 'auto',
                  backgroundColor: 'rgba(0,0,0,0.2)',
                  p: 2,
                  borderRadius: 1
                }}
              >
                {agent.full_instructions || agent.instructions_preview}
              </Box>
            </AccordionDetails>
          </Accordion>
        )}
      </CardContent>
    </Card>
  );
}

export function AgentContextView({ data }: AgentContextViewProps) {
  if (!data.found) {
    return (
      <Paper sx={{ p: 3 }}>
        <Typography color="text.secondary" fontStyle="italic">
          No agent context found in this trace
        </Typography>
      </Paper>
    );
  }

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 3 }}>
        <Typography variant="h5">Agent Context</Typography>
        <Box sx={{ display: 'flex', gap: 1 }}>
          {data.trace_metadata.supervisor_agent && (
            <Chip label={data.trace_metadata.supervisor_agent} color="primary" />
          )}
          {data.trace_metadata.has_document && (
            <Chip label="Has Document" color="info" variant="outlined" />
          )}
        </Box>
      </Box>

      {/* Trace Metadata */}
      <Paper sx={{ p: 2, mb: 3 }}>
        <Typography variant="h6" gutterBottom>Trace Metadata</Typography>
        <Grid container spacing={2}>
          <Grid item xs={12} md={4}>
            <Typography color="text.secondary" variant="body2">Supervisor Agent</Typography>
            <Typography variant="body1">{data.trace_metadata.supervisor_agent || 'N/A'}</Typography>
          </Grid>
          <Grid item xs={12} md={4}>
            <Typography color="text.secondary" variant="body2">Supervisor Model</Typography>
            <Typography variant="body1">{data.trace_metadata.supervisor_model || 'N/A'}</Typography>
          </Grid>
          <Grid item xs={12} md={4}>
            <Typography color="text.secondary" variant="body2">Has Document</Typography>
            <Typography variant="body1">{data.trace_metadata.has_document ? 'Yes' : 'No'}</Typography>
          </Grid>
        </Grid>
      </Paper>

      {/* Supervisor Agent */}
      {data.supervisor && (
        <AgentCard agent={data.supervisor} title="Supervisor Agent" />
      )}

      {/* Specialist Agents */}
      {data.specialists.length > 0 && (
        <Box sx={{ mb: 3 }}>
          <Typography variant="h6" gutterBottom sx={{ mt: 3 }}>
            Specialist Agents ({data.specialists.length})
          </Typography>
          {data.specialists.map((specialist, index) => (
            <AgentCard
              key={`${specialist.agent_type}-${index}`}
              agent={specialist}
              title={`Specialist: ${specialist.agent_type}`}
            />
          ))}
        </Box>
      )}

      {/* All Tools */}
      <Paper sx={{ p: 2 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
          <BuildIcon color="primary" />
          <Typography variant="h6">All Available Tools ({data.all_tools.length})</Typography>
        </Box>
        <TableContainer sx={{ maxHeight: 400 }}>
          <Table size="small" stickyHeader>
            <TableHead>
              <TableRow>
                <TableCell>Tool Name</TableCell>
                <TableCell>Description</TableCell>
                <TableCell>Strict</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data.all_tools.map((tool) => (
                <TableRow key={tool.name}>
                  <TableCell>
                    <Chip label={tool.name} size="small" color="primary" variant="outlined" />
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2" sx={{ maxWidth: 400, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {tool.description || 'No description'}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    {tool.strict ? (
                      <Chip label="Yes" size="small" color="success" />
                    ) : (
                      <Chip label="No" size="small" variant="outlined" />
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>
    </Box>
  );
}
