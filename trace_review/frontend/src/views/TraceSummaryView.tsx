import { Box, Typography, Paper, Chip, Card, CardContent, Grid, Alert, Link, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Tooltip } from '@mui/material';
import AccessTimeIcon from '@mui/icons-material/AccessTime';
import AttachMoneyIcon from '@mui/icons-material/AttachMoney';
import WarningIcon from '@mui/icons-material/Warning';
import BuildIcon from '@mui/icons-material/Build';
import QuestionAnswerIcon from '@mui/icons-material/QuestionAnswer';
import BiotechIcon from '@mui/icons-material/Biotech';
import { TraceSummaryData } from '../types';

interface TraceSummaryViewProps {
  data: TraceSummaryData;
}

export function TraceSummaryView({ data }: TraceSummaryViewProps) {
  const formatCost = (n: number) => `$${n.toFixed(6)}`;
  const formatTokens = (n: number) => n.toLocaleString();

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 3 }}>
        <Typography variant="h5">Trace Summary</Typography>
        <Box sx={{ display: 'flex', gap: 1 }}>
          {data.has_errors && (
            <Chip icon={<WarningIcon />} label="Has Errors" color="error" />
          )}
          {data.context_overflow_detected && (
            <Chip icon={<WarningIcon />} label="Context Overflow" color="warning" />
          )}
          {data.trace_info.bookmarked && (
            <Chip label="Bookmarked" color="secondary" />
          )}
        </Box>
      </Box>

      {/* Errors Alert */}
      {data.has_errors && data.errors.length > 0 && (
        <Alert severity="error" sx={{ mb: 3 }}>
          <Typography variant="subtitle2" fontWeight="bold" gutterBottom>
            {data.errors.length} Error(s) Detected
          </Typography>
          {data.errors.map((error, index) => (
            <Typography key={index} variant="body2">
              [{error.type}] {error.message}
            </Typography>
          ))}
        </Alert>
      )}

      {/* Query and Response */}
      <Paper sx={{ p: 2, mb: 3 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
          <QuestionAnswerIcon color="primary" />
          <Typography variant="h6">Query & Response</Typography>
        </Box>
        <Box sx={{ mb: 2 }}>
          <Typography color="text.secondary" variant="body2" gutterBottom>Query</Typography>
          <Paper
            sx={{
              p: 2,
              backgroundColor: 'rgba(33, 150, 243, 0.1)',
              borderLeft: '4px solid',
              borderLeftColor: 'primary.main'
            }}
          >
            <Typography variant="body1">{data.query}</Typography>
          </Paper>
        </Box>
        {(data.response_preview || data.response) && (
          <Box>
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
              <Typography color="text.secondary" variant="body2">Response Preview</Typography>
              {data.response_length && data.response_length > 0 && (
                <Chip
                  label={`${data.response_length.toLocaleString()} chars`}
                  size="small"
                  variant="outlined"
                />
              )}
            </Box>
            <Paper
              sx={{
                p: 2,
                backgroundColor: 'rgba(76, 175, 80, 0.1)',
                borderLeft: '4px solid',
                borderLeftColor: 'success.main',
                maxHeight: 300,
                overflow: 'auto'
              }}
            >
              <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', lineHeight: 1.7 }}>
                {data.response_preview || data.response}
              </Typography>
            </Paper>
          </Box>
        )}
      </Paper>

      {/* Stats Grid */}
      <Grid container spacing={2} sx={{ mb: 3 }}>
        {/* Timing Card */}
        <Grid item xs={12} md={4}>
          <Card>
            <CardContent>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
                <AccessTimeIcon color="primary" />
                <Typography variant="h6">Timing</Typography>
              </Box>
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
                <Box>
                  <Typography color="text.secondary" variant="body2">Total Latency</Typography>
                  <Typography variant="h6">{data.timing.total_latency_seconds?.toFixed(2) || 0}s</Typography>
                </Box>
                <Box>
                  <Typography color="text.secondary" variant="body2">Started</Typography>
                  <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
                    {new Date(data.timing.created_at).toLocaleString()}
                  </Typography>
                </Box>
              </Box>
            </CardContent>
          </Card>
        </Grid>

        {/* Cost Card */}
        <Grid item xs={12} md={4}>
          <Card>
            <CardContent>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
                <AttachMoneyIcon color="success" />
                <Typography variant="h6">Cost</Typography>
              </Box>
              <Typography variant="h4" color="success.main">
                {formatCost(data.cost.total_cost)}
              </Typography>
              <Typography color="text.secondary" variant="body2">
                {data.cost.currency}
              </Typography>
            </CardContent>
          </Card>
        </Grid>

        {/* Generations Card */}
        <Grid item xs={12} md={4}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>Generation Stats</Typography>
              <Grid container spacing={1}>
                <Grid item xs={6}>
                  <Typography color="text.secondary" variant="body2">Generations</Typography>
                  <Typography variant="h6">{data.generation_stats.total_generations}</Typography>
                </Grid>
                <Grid item xs={6}>
                  <Typography color="text.secondary" variant="body2">Total Tokens</Typography>
                  <Typography variant="h6">{formatTokens(data.generation_stats.total_tokens)}</Typography>
                </Grid>
                <Grid item xs={6}>
                  <Typography color="text.secondary" variant="body2">Prompt</Typography>
                  <Typography variant="body1">{formatTokens(data.generation_stats.total_prompt_tokens)}</Typography>
                </Grid>
                <Grid item xs={6}>
                  <Typography color="text.secondary" variant="body2">Completion</Typography>
                  <Typography variant="body1">{formatTokens(data.generation_stats.total_completion_tokens)}</Typography>
                </Grid>
              </Grid>
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* Models Used */}
      <Paper sx={{ p: 2, mb: 3 }}>
        <Typography variant="h6" gutterBottom>Models Used</Typography>
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
          {Object.entries(data.generation_stats.models_used).map(([model, count]) => (
            <Chip
              key={model}
              label={`${model}: ${count}`}
              variant="outlined"
              color="secondary"
            />
          ))}
        </Box>
      </Paper>

      {/* Tool Summary */}
      <Paper sx={{ p: 2, mb: 3 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
          <BuildIcon color="primary" />
          <Typography variant="h6">Tool Usage</Typography>
          <Chip label={`${data.tool_summary.total_tool_calls} calls`} size="small" color="primary" />
        </Box>
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Tool</TableCell>
                <TableCell align="right">Calls</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {Object.entries(data.tool_summary.tool_counts)
                .sort(([, a], [, b]) => b - a)
                .map(([tool, count]) => (
                  <TableRow key={tool}>
                    <TableCell>
                      <Chip label={tool} size="small" variant="outlined" />
                    </TableCell>
                    <TableCell align="right">
                      <Chip label={count} size="small" color={count > 5 ? 'warning' : 'default'} />
                    </TableCell>
                  </TableRow>
                ))}
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>

      {/* Trace Info */}
      <Paper sx={{ p: 2, mb: 3 }}>
        <Typography variant="h6" gutterBottom>Trace Info</Typography>
        <Grid container spacing={2}>
          <Grid item xs={12} md={6}>
            <Typography color="text.secondary" variant="body2">Trace ID</Typography>
            <Typography variant="body1" sx={{ fontFamily: 'monospace', wordBreak: 'break-all' }}>
              {data.trace_info.trace_id}
            </Typography>
          </Grid>
          <Grid item xs={12} md={6}>
            <Typography color="text.secondary" variant="body2">Name</Typography>
            <Typography variant="body1">{data.trace_info.name || 'N/A'}</Typography>
          </Grid>
          {data.trace_info.session_id && (
            <Grid item xs={12} md={6}>
              <Typography color="text.secondary" variant="body2">Session ID</Typography>
              <Typography variant="body1" sx={{ fontFamily: 'monospace' }}>
                {data.trace_info.session_id}
              </Typography>
            </Grid>
          )}
          {data.trace_info.user_id && (
            <Grid item xs={12} md={6}>
              <Typography color="text.secondary" variant="body2">User ID</Typography>
              <Typography variant="body1">{data.trace_info.user_id}</Typography>
            </Grid>
          )}
          {data.trace_info.tags.length > 0 && (
            <Grid item xs={12}>
              <Typography color="text.secondary" variant="body2" gutterBottom>Tags</Typography>
              <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                {data.trace_info.tags.map((tag) => (
                  <Chip key={tag} label={tag} size="small" />
                ))}
              </Box>
            </Grid>
          )}
        </Grid>
      </Paper>

      {/* Agent Info */}
      {data.agent_info && (
        <Paper sx={{ p: 2, mb: 3 }}>
          <Typography variant="h6" gutterBottom>Agent Info</Typography>
          <Grid container spacing={2}>
            <Grid item xs={12} md={4}>
              <Typography color="text.secondary" variant="body2">Supervisor Agent</Typography>
              <Typography variant="body1">{data.agent_info.supervisor_agent || 'N/A'}</Typography>
            </Grid>
            <Grid item xs={12} md={4}>
              <Typography color="text.secondary" variant="body2">Supervisor Model</Typography>
              <Typography variant="body1">{data.agent_info.supervisor_model || 'N/A'}</Typography>
            </Grid>
            <Grid item xs={12} md={4}>
              <Typography color="text.secondary" variant="body2">Has Document</Typography>
              <Typography variant="body1">{data.agent_info.has_document ? 'Yes' : 'No'}</Typography>
            </Grid>
          </Grid>
        </Paper>
      )}

      {/* MOD Context */}
      {data.mod_context && data.mod_context.injection_active && (
        <Paper sx={{ p: 2, mb: 3 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
            <BiotechIcon color="primary" />
            <Typography variant="h6">MOD Context</Typography>
            <Chip
              label={`${data.mod_context.mod_count} MOD${data.mod_context.mod_count > 1 ? 's' : ''} Active`}
              size="small"
              color="success"
            />
          </Box>
          <Typography color="text.secondary" variant="body2" gutterBottom>
            Model Organism Database-specific rules applied to this session
          </Typography>
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1, mt: 1 }}>
            {data.mod_context.mod_details ? (
              data.mod_context.mod_details.map((mod) => (
                <Tooltip key={mod.mod_id} title={mod.description} arrow>
                  <Chip
                    label={mod.mod_id}
                    color="primary"
                    variant="outlined"
                    icon={<BiotechIcon />}
                  />
                </Tooltip>
              ))
            ) : (
              data.mod_context.active_mods.map((mod) => (
                <Chip
                  key={mod}
                  label={mod}
                  color="primary"
                  variant="outlined"
                  icon={<BiotechIcon />}
                />
              ))
            )}
          </Box>
        </Paper>
      )}

      {/* Document Info */}
      {data.document && (
        <Paper sx={{ p: 2, mb: 3 }}>
          <Typography variant="h6" gutterBottom>Document</Typography>
          <Grid container spacing={2}>
            <Grid item xs={12} md={6}>
              <Typography color="text.secondary" variant="body2">Document ID</Typography>
              <Typography variant="body1" sx={{ fontFamily: 'monospace' }}>
                {data.document.id}
              </Typography>
            </Grid>
            <Grid item xs={12} md={6}>
              <Typography color="text.secondary" variant="body2">Document Name</Typography>
              <Typography variant="body1">{data.document.name}</Typography>
            </Grid>
          </Grid>
        </Paper>
      )}

      {/* Links */}
      {data.links.langfuse_trace && (
        <Paper sx={{ p: 2 }}>
          <Typography variant="h6" gutterBottom>Links</Typography>
          <Link href={data.links.langfuse_trace} target="_blank" rel="noopener noreferrer">
            Open in Langfuse
          </Link>
        </Paper>
      )}
    </Box>
  );
}
