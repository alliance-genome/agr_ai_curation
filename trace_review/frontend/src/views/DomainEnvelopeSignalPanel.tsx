import {
  Box,
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
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import BuildCircleIcon from '@mui/icons-material/BuildCircle';
import FlagIcon from '@mui/icons-material/Flag';
import ReportProblemIcon from '@mui/icons-material/ReportProblem';
import { alpha } from '@mui/material/styles';
import { DomainEnvelopeTraceSummary } from '../types';

interface DomainEnvelopeSignalPanelProps {
  summary?: DomainEnvelopeTraceSummary;
  title?: string;
  dense?: boolean;
  variant?: 'paper' | 'inline';
}

export function hasDomainEnvelopeSignals(summary?: DomainEnvelopeTraceSummary): summary is DomainEnvelopeTraceSummary {
  return Boolean(summary?.found);
}

export function domainEnvelopeCountChips(summary: DomainEnvelopeTraceSummary): Array<{ label: string; color: 'primary' | 'secondary' | 'warning' | 'error' | 'success' | 'info' | 'default' }> {
  const counts = summary.summary ?? {};
  const envelopeCount = counts.envelope_count ?? 0;
  const objectCount = counts.object_count ?? 0;
  const findingCount = counts.finding_count ?? 0;
  const repairAttemptCount = counts.repair_attempt_count ?? 0;
  const blockerCount = counts.blocker_count ?? 0;
  return [
    { label: `${envelopeCount} envelopes`, color: 'primary' },
    { label: `${objectCount} objects`, color: 'secondary' },
    { label: `${findingCount} findings`, color: findingCount > 0 ? 'warning' : 'default' },
    { label: `${repairAttemptCount} repairs`, color: repairAttemptCount > 0 ? 'info' : 'default' },
    { label: `${blockerCount} blockers`, color: blockerCount > 0 ? 'error' : 'default' },
  ];
}

function LimitedChips({
  values,
  color = 'default',
  max = 8,
}: {
  values?: string[];
  color?: 'primary' | 'secondary' | 'warning' | 'error' | 'success' | 'info' | 'default';
  max?: number;
}) {
  const items = (values ?? []).filter(Boolean);
  if (items.length === 0) return null;

  return (
    <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.75 }}>
      {items.slice(0, max).map((value) => (
        <Chip
          key={value}
          label={value}
          size="small"
          color={color}
          variant="outlined"
          sx={{ fontFamily: 'monospace', maxWidth: 280 }}
        />
      ))}
      {items.length > max && (
        <Chip label={`+${items.length - max}`} size="small" variant="outlined" />
      )}
    </Box>
  );
}

function CountMapChips({ values }: { values?: Record<string, number> }) {
  const entries = Object.entries(values ?? {}).filter(([, count]) => count > 0);
  if (entries.length === 0) return null;
  return (
    <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.75 }}>
      {entries.map(([state, count]) => (
        <Chip key={state} label={`${state}: ${count}`} size="small" variant="outlined" />
      ))}
    </Box>
  );
}

function RepairRows({ summary, dense }: { summary: DomainEnvelopeTraceSummary; dense?: boolean }) {
  const repairs = summary.repair_attempts ?? [];
  if (repairs.length === 0) return null;

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
        <BuildCircleIcon color="info" fontSize="small" />
        <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>Repair Loop</Typography>
      </Box>
      <TableContainer>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Action</TableCell>
              <TableCell>Envelope</TableCell>
              {!dense && <TableCell>Finding</TableCell>}
              <TableCell>Field Path</TableCell>
              {!dense && <TableCell>Attempts</TableCell>}
            </TableRow>
          </TableHead>
          <TableBody>
            {repairs.slice(0, dense ? 3 : 8).map((repair, index) => (
              <TableRow key={`${repair.repair_action}-${repair.patch_id || repair.event_id || index}`}>
                <TableCell>
                  <Chip label={repair.repair_action} size="small" color="info" variant="outlined" />
                </TableCell>
                <TableCell sx={{ fontFamily: 'monospace' }}>{repair.envelope_id || 'N/A'}</TableCell>
                {!dense && (
                  <TableCell sx={{ fontFamily: 'monospace' }}>
                    {(repair.finding_ids ?? []).join(', ') || 'N/A'}
                  </TableCell>
                )}
                <TableCell sx={{ fontFamily: 'monospace' }}>
                  {(repair.field_paths ?? []).join(', ') || 'N/A'}
                </TableCell>
                {!dense && (
                  <TableCell>
                    {repair.retry_budget ? JSON.stringify(repair.retry_budget) : 'N/A'}
                  </TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
    </Box>
  );
}

function BlockerRows({ summary, dense }: { summary: DomainEnvelopeTraceSummary; dense?: boolean }) {
  const blockers = summary.blockers ?? [];
  if (blockers.length === 0) return null;

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
        <ReportProblemIcon color="error" fontSize="small" />
        <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>Blockers</Typography>
      </Box>
      <TableContainer>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Code</TableCell>
              <TableCell>Object</TableCell>
              <TableCell>Field Path</TableCell>
              {!dense && <TableCell>Message</TableCell>}
            </TableRow>
          </TableHead>
          <TableBody>
            {blockers.slice(0, dense ? 3 : 8).map((blocker, index) => (
              <TableRow key={`${blocker.code || blocker.status}-${blocker.field_path || index}`}>
                <TableCell>
                  <Chip label={blocker.code || blocker.status || blocker.severity || 'blocker'} size="small" color="error" variant="outlined" />
                </TableCell>
                <TableCell sx={{ fontFamily: 'monospace' }}>
                  {blocker.object_id || blocker.pending_ref_id || 'N/A'}
                </TableCell>
                <TableCell sx={{ fontFamily: 'monospace' }}>{blocker.field_path || 'N/A'}</TableCell>
                {!dense && <TableCell>{blocker.message || 'N/A'}</TableCell>}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
    </Box>
  );
}

function DefinitionStateRows({ summary }: { summary: DomainEnvelopeTraceSummary }) {
  const flags = summary.definition_state_flags ?? [];
  if (flags.length === 0) return null;

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
        <FlagIcon color="warning" fontSize="small" />
        <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>Definition State</Typography>
      </Box>
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
        {flags.slice(0, 8).map((flag, index) => (
          <Box key={`${flag.source}-${flag.object_id || flag.pending_ref_id || index}`} sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
            <Chip label={flag.definition_state} size="small" color="warning" variant="outlined" />
            <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
              {[flag.envelope_id, flag.object_id || flag.pending_ref_id, flag.field_path].filter(Boolean).join(' / ') || flag.source}
            </Typography>
          </Box>
        ))}
      </Box>
    </Box>
  );
}

export function DomainEnvelopeSignalPanel({
  summary,
  title = 'Domain Envelope',
  dense = false,
  variant = 'paper',
}: DomainEnvelopeSignalPanelProps) {
  if (!hasDomainEnvelopeSignals(summary)) return null;

  const counts = domainEnvelopeCountChips(summary);
  const content = (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: dense ? 2 : 3 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
        <AccountTreeIcon color="primary" />
        <Typography variant={dense ? 'subtitle1' : 'h6'} sx={{ fontWeight: 600 }}>{title}</Typography>
        {counts.map((chip) => (
          <Chip key={chip.label} label={chip.label} size="small" color={chip.color} />
        ))}
      </Box>

      <Grid container spacing={2}>
        <Grid item xs={12} md={6}>
          <Typography variant="caption" color="text.secondary">Envelope IDs</Typography>
          <LimitedChips values={summary.envelope_ids} color="primary" max={dense ? 3 : 8} />
        </Grid>
        <Grid item xs={12} md={6}>
          <Typography variant="caption" color="text.secondary">Object IDs</Typography>
          <LimitedChips values={[...(summary.object_ids ?? []), ...(summary.pending_ref_ids ?? [])]} color="secondary" max={dense ? 3 : 8} />
        </Grid>
        <Grid item xs={12} md={6}>
          <Typography variant="caption" color="text.secondary">Finding IDs</Typography>
          <LimitedChips values={summary.finding_ids} color="warning" max={dense ? 3 : 8} />
        </Grid>
        <Grid item xs={12} md={6}>
          <Typography variant="caption" color="text.secondary">Field Paths</Typography>
          <LimitedChips values={summary.field_paths} color="info" max={dense ? 3 : 8} />
        </Grid>
        {!dense && (
          <>
            <Grid item xs={12} md={6}>
              <Typography variant="caption" color="text.secondary">Validation States</Typography>
              <CountMapChips values={summary.validation_state_counts} />
            </Grid>
            <Grid item xs={12} md={6}>
              <Typography variant="caption" color="text.secondary">Definition States</Typography>
              <CountMapChips values={summary.definition_state_counts} />
            </Grid>
          </>
        )}
      </Grid>

      <RepairRows summary={summary} dense={dense} />
      <BlockerRows summary={summary} dense={dense} />
      {!dense && <DefinitionStateRows summary={summary} />}
    </Box>
  );

  if (variant === 'inline') {
    return (
      <Box
        sx={(theme) => ({
          p: 2,
          borderRadius: 1,
          backgroundColor: alpha(theme.palette.primary.main, theme.palette.mode === 'dark' ? 0.1 : 0.06),
          border: '1px solid',
          borderColor: 'divider',
        })}
      >
        {content}
      </Box>
    );
  }

  return <Paper sx={{ p: 2, mb: 3 }}>{content}</Paper>;
}
