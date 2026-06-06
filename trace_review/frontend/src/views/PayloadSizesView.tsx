import {
  Alert,
  Box,
  Card,
  CardContent,
  Chip,
  Grid,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';
import DataObjectIcon from '@mui/icons-material/DataObject';
import WarningIcon from '@mui/icons-material/Warning';
import { ExtractionTimelineData, PayloadSizeEvent } from '../types';

interface PayloadSizesViewProps {
  data: ExtractionTimelineData;
}

function formatCount(value: number | undefined) {
  return (value || 0).toLocaleString();
}

function formatApproxTokens(value: number | undefined) {
  return `${formatCount(value)} est. tokens`;
}

function formatDirection(direction: PayloadSizeEvent['direction']) {
  if (direction === 'input') return 'Input';
  if (direction === 'output') return 'Output';
  return 'Event';
}

export function PayloadSizesView({ data }: PayloadSizesViewProps) {
  const summary = data.size_summary;

  if (!summary) {
    return (
      <Paper sx={{ p: 3 }}>
        <Typography color="text.secondary" fontStyle="italic">
          No payload size data found for this trace
        </Typography>
      </Paper>
    );
  }

  const millionPlusCount = summary.threshold_counts?.['1000000'] || 0;

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 3 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <DataObjectIcon color="primary" />
          <Typography variant="h5">Payload Sizes</Typography>
        </Box>
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
          <Chip label={`${formatCount(data.event_count)} events`} color="primary" />
          <Chip label={formatApproxTokens(summary.estimated_exchange_tokens)} color="secondary" />
          {millionPlusCount > 0 && (
            <Chip icon={<WarningIcon />} label={`${millionPlusCount} over 1M chars`} color="error" />
          )}
        </Box>
      </Box>

      {millionPlusCount > 0 && (
        <Alert severity="error" sx={{ mb: 3 }}>
          <Typography variant="subtitle2" fontWeight="bold">
            Very large payloads detected
          </Typography>
          <Typography variant="body2">
            {millionPlusCount} payload event(s) crossed 1,000,000 JSON characters. Check the ranked table below first.
          </Typography>
        </Alert>
      )}

      <Grid container spacing={2} sx={{ mb: 3 }}>
        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Typography color="text.secondary" variant="body2">Input JSON</Typography>
              <Typography variant="h6">{formatCount(summary.input_json_chars)} chars</Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Typography color="text.secondary" variant="body2">Output JSON</Typography>
              <Typography variant="h6">{formatCount(summary.output_json_chars)} chars</Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Typography color="text.secondary" variant="body2">Exchange Estimate</Typography>
              <Typography variant="h6">{formatApproxTokens(summary.estimated_exchange_tokens)}</Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Typography color="text.secondary" variant="body2">Persisted Event JSON</Typography>
              <Typography variant="h6">{formatCount(summary.event_json_chars)} chars</Typography>
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      <Paper sx={{ p: 2, mb: 3 }}>
        <Typography variant="h6" gutterBottom>Largest Payloads</Typography>
        <TableContainer sx={{ maxHeight: 520 }}>
          <Table size="small" stickyHeader>
            <TableHead>
              <TableRow>
                <TableCell>Rank</TableCell>
                <TableCell>Direction</TableCell>
                <TableCell>Event</TableCell>
                <TableCell>Tool</TableCell>
                <TableCell>Agent</TableCell>
                <TableCell align="right">Chars</TableCell>
                <TableCell align="right">Est. Tokens</TableCell>
                <TableCell>Source</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {summary.largest_events.map((event) => {
                const isLarge = event.json_chars >= 1_000_000;
                return (
                  <TableRow
                    key={`${event.rank}-${event.direction}-${event.event_id || event.sequence}`}
                    sx={{
                      bgcolor: isLarge ? 'error.dark' : 'inherit',
                      '&:hover': { bgcolor: isLarge ? 'error.main' : 'action.hover' },
                    }}
                  >
                    <TableCell>
                      <Chip label={`#${event.rank}`} size="small" color={isLarge ? 'error' : 'default'} />
                    </TableCell>
                    <TableCell>{formatDirection(event.direction)}</TableCell>
                    <TableCell>
                      <Typography variant="body2">{event.event_type || 'unknown'}</Typography>
                      <Typography variant="caption" color="text.secondary">
                        Seq {event.sequence || 'n/a'} / Trace {event.event_trace_id || 'n/a'}
                      </Typography>
                    </TableCell>
                    <TableCell>{event.tool_name || 'n/a'}</TableCell>
                    <TableCell>{event.agent || 'n/a'}</TableCell>
                    <TableCell align="right" sx={{ fontFamily: 'monospace' }}>
                      {formatCount(event.json_chars)}
                    </TableCell>
                    <TableCell align="right" sx={{ fontFamily: 'monospace' }}>
                      {formatCount(event.estimated_tokens)}
                    </TableCell>
                    <TableCell>{event.source || 'n/a'}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>

      <Paper sx={{ p: 2 }}>
        <Typography variant="h6" gutterBottom>Event Type Totals</Typography>
        <TableContainer sx={{ maxHeight: 420 }}>
          <Table size="small" stickyHeader>
            <TableHead>
              <TableRow>
                <TableCell>Event Type</TableCell>
                <TableCell align="right">Count</TableCell>
                <TableCell align="right">Input Chars</TableCell>
                <TableCell align="right">Output Chars</TableCell>
                <TableCell align="right">Max Chars</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {Object.entries(summary.by_event_type)
                .sort(([, a], [, b]) => b.max_json_chars - a.max_json_chars)
                .map(([eventType, stats]) => (
                  <TableRow key={eventType}>
                    <TableCell>{eventType}</TableCell>
                    <TableCell align="right">{formatCount(stats.event_count)}</TableCell>
                    <TableCell align="right" sx={{ fontFamily: 'monospace' }}>
                      {formatCount(stats.input_json_chars)}
                    </TableCell>
                    <TableCell align="right" sx={{ fontFamily: 'monospace' }}>
                      {formatCount(stats.output_json_chars)}
                    </TableCell>
                    <TableCell align="right" sx={{ fontFamily: 'monospace' }}>
                      {formatCount(stats.max_json_chars)}
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
