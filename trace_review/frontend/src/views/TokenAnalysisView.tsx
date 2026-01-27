import { Box, Typography, Paper, Chip, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Alert, Card, CardContent, Grid } from '@mui/material';
import WarningIcon from '@mui/icons-material/Warning';
import TrendingUpIcon from '@mui/icons-material/TrendingUp';
import { TokenAnalysisData } from '../types';

interface TokenAnalysisViewProps {
  data: TokenAnalysisData;
}

export function TokenAnalysisView({ data }: TokenAnalysisViewProps) {
  if (!data.found) {
    return (
      <Paper sx={{ p: 3 }}>
        <Typography color="text.secondary" fontStyle="italic">
          No token data found in this trace
        </Typography>
      </Paper>
    );
  }

  const formatTokens = (n: number) => n.toLocaleString();
  const formatCost = (n: number) => `$${n.toFixed(6)}`;

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 3 }}>
        <Typography variant="h5">Token Analysis</Typography>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Chip label={`${data.total_generations} Generations`} color="primary" />
          <Chip label={formatCost(data.total_cost)} color="success" />
          {data.context_overflow_detected && (
            <Chip
              icon={<WarningIcon />}
              label="Context Overflow"
              color="error"
            />
          )}
        </Box>
      </Box>

      {/* Context Overflow Alert */}
      {data.context_overflow_detected && data.context_overflow_details && (
        <Alert severity="error" sx={{ mb: 3 }}>
          <Typography variant="subtitle2" fontWeight="bold">
            Context Overflow Detected!
          </Typography>
          <Typography variant="body2">
            Generation #{data.context_overflow_details.generation} exceeded context limit with{' '}
            {formatTokens(data.context_overflow_details.prompt_tokens)} tokens on model{' '}
            {data.context_overflow_details.model}
          </Typography>
        </Alert>
      )}

      {/* Summary Cards */}
      <Grid container spacing={2} sx={{ mb: 3 }}>
        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Typography color="text.secondary" variant="body2">Total Cost</Typography>
              <Typography variant="h6">{formatCost(data.total_cost)}</Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Typography color="text.secondary" variant="body2">Total Latency</Typography>
              <Typography variant="h6">{data.total_latency?.toFixed(2) || 0}s</Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Typography color="text.secondary" variant="body2">Prompt Tokens</Typography>
              <Typography variant="h6">{formatTokens(data.total_prompt_tokens)}</Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Typography color="text.secondary" variant="body2">Completion Tokens</Typography>
              <Typography variant="h6">{formatTokens(data.total_completion_tokens)}</Typography>
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* Model Breakdown */}
      <Paper sx={{ p: 2, mb: 3 }}>
        <Typography variant="h6" gutterBottom>Model Breakdown</Typography>
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Model</TableCell>
                <TableCell align="right">Count</TableCell>
                <TableCell align="right">Prompt Tokens</TableCell>
                <TableCell align="right">Completion Tokens</TableCell>
                <TableCell align="right">Cost</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {Object.entries(data.model_breakdown).map(([model, stats]) => (
                <TableRow key={model}>
                  <TableCell>
                    <Chip label={model} size="small" variant="outlined" />
                  </TableCell>
                  <TableCell align="right">{stats.count}</TableCell>
                  <TableCell align="right">{formatTokens(stats.prompt_tokens)}</TableCell>
                  <TableCell align="right">{formatTokens(stats.completion_tokens)}</TableCell>
                  <TableCell align="right">{formatCost(stats.total_cost)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>

      {/* Context Growth */}
      <Paper sx={{ p: 2, mb: 3 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
          <TrendingUpIcon color="primary" />
          <Typography variant="h6">Context Growth</Typography>
        </Box>
        <TableContainer sx={{ maxHeight: 400 }}>
          <Table size="small" stickyHeader>
            <TableHead>
              <TableRow>
                <TableCell>Gen #</TableCell>
                <TableCell align="right">Prompt Tokens</TableCell>
                <TableCell align="right">Delta</TableCell>
                <TableCell>Growth</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data.context_growth.map((entry) => {
                const isHighDelta = entry.delta > 10000;
                const isOverflow = data.context_overflow_details?.generation === entry.generation;
                return (
                  <TableRow
                    key={entry.generation}
                    sx={{
                      backgroundColor: isOverflow ? 'error.dark' : isHighDelta ? 'warning.dark' : 'inherit',
                      '&:hover': { backgroundColor: isOverflow ? 'error.main' : isHighDelta ? 'warning.main' : 'action.hover' }
                    }}
                  >
                    <TableCell>
                      <Chip
                        label={`#${entry.generation}`}
                        size="small"
                        color={isOverflow ? 'error' : isHighDelta ? 'warning' : 'default'}
                      />
                    </TableCell>
                    <TableCell align="right" sx={{ fontFamily: 'monospace' }}>
                      {formatTokens(entry.prompt_tokens)}
                    </TableCell>
                    <TableCell align="right" sx={{ fontFamily: 'monospace' }}>
                      <Typography
                        component="span"
                        color={entry.delta > 5000 ? 'warning.main' : 'success.main'}
                      >
                        +{formatTokens(entry.delta)}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Box
                        sx={{
                          height: 8,
                          backgroundColor: entry.delta > 10000 ? 'warning.main' : 'primary.main',
                          borderRadius: 1,
                          width: `${Math.min(100, (entry.delta / 1000) * 5)}%`,
                          minWidth: 4
                        }}
                      />
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>

      {/* Generation Details */}
      <Paper sx={{ p: 2 }}>
        <Typography variant="h6" gutterBottom>Generation Details</Typography>
        <TableContainer sx={{ maxHeight: 500 }}>
          <Table size="small" stickyHeader>
            <TableHead>
              <TableRow>
                <TableCell>Gen #</TableCell>
                <TableCell>Model</TableCell>
                <TableCell>Type</TableCell>
                <TableCell align="right">Prompt</TableCell>
                <TableCell align="right">Completion</TableCell>
                <TableCell align="right">Cost</TableCell>
                <TableCell align="right">Duration</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data.generations.map((gen) => (
                <TableRow key={gen.generation}>
                  <TableCell>
                    <Chip label={`#${gen.generation}`} size="small" />
                  </TableCell>
                  <TableCell>
                    <Chip label={gen.model} size="small" variant="outlined" color="secondary" />
                  </TableCell>
                  <TableCell>
                    <Chip
                      label={gen.tool_name || gen.output_type}
                      size="small"
                      color={gen.output_type === 'function_call' ? 'info' : 'default'}
                    />
                  </TableCell>
                  <TableCell align="right" sx={{ fontFamily: 'monospace' }}>
                    {formatTokens(gen.prompt_tokens)}
                  </TableCell>
                  <TableCell align="right" sx={{ fontFamily: 'monospace' }}>
                    {formatTokens(gen.completion_tokens)}
                  </TableCell>
                  <TableCell align="right" sx={{ fontFamily: 'monospace' }}>
                    {formatCost(gen.cost)}
                  </TableCell>
                  <TableCell align="right" sx={{ fontFamily: 'monospace' }}>
                    {gen.duration_ms ? `${gen.duration_ms}ms` : 'N/A'}
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
