import { Box, Card, CardContent, Typography, Grid, Chip, Paper, IconButton, Tooltip } from '@mui/material';
import AccessTimeIcon from '@mui/icons-material/AccessTime';
import AttachMoneyIcon from '@mui/icons-material/AttachMoney';
import TokenIcon from '@mui/icons-material/Token';
import VisibilityIcon from '@mui/icons-material/Visibility';
import AssessmentIcon from '@mui/icons-material/Assessment';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import CheckIcon from '@mui/icons-material/Check';
import { useState } from 'react';
import { SummaryData } from '../types';

interface SummaryViewProps {
  data: SummaryData;
}

export function SummaryView({ data }: SummaryViewProps) {
  const [copied, setCopied] = useState(false);

  const handleCopyTraceId = async () => {
    await navigator.clipboard.writeText(data.trace_id);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  // Color-code system domain
  const getDomainColor = (domain?: string): "default" | "primary" | "secondary" | "warning" | "success" | "info" => {
    if (!domain) return 'default';
    if (domain.includes('internal_db')) return 'primary';
    if (domain.includes('external_db')) return 'secondary';
    if (domain.includes('pdf')) return 'warning';
    return 'info';
  };

  const getDomainLabel = (domain?: string) => {
    if (!domain || domain === 'unknown') return 'Unknown';
    if (domain.includes('internal_db')) return 'Internal DB';
    if (domain.includes('external_db')) return 'External DB';
    if (domain.includes('pdf')) return 'PDF Only';
    return domain;
  };

  const formatDuration = (seconds: number) => {
    return `${seconds.toFixed(2)}s`;
  };

  const formatCost = (cost: number) => {
    if (cost < 0.01) return `$${cost.toFixed(6)}`;
    return `$${cost.toFixed(4)}`;
  };

  return (
    <Box>
      {/* Header with system domain */}
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 3 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
          <AssessmentIcon color="primary" fontSize="large" />
          <Typography variant="h5">Trace Summary</Typography>
        </Box>
        {data.system_domain && (
          <Chip
            label={getDomainLabel(data.system_domain)}
            color={getDomainColor(data.system_domain)}
            size="medium"
            sx={{ fontWeight: 'bold' }}
          />
        )}
      </Box>

      {/* Key Metrics Cards */}
      <Grid container spacing={2} sx={{ mb: 3 }}>
        {/* Duration */}
        <Grid item xs={6} sm={3}>
          <Card sx={{ height: '100%' }}>
            <CardContent>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
                <AccessTimeIcon color="primary" fontSize="small" />
                <Typography color="text.secondary" variant="body2">Duration</Typography>
              </Box>
              <Typography variant="h5" sx={{ fontWeight: 600 }}>
                {formatDuration(data.duration_seconds || 0)}
              </Typography>
            </CardContent>
          </Card>
        </Grid>

        {/* Cost */}
        <Grid item xs={6} sm={3}>
          <Card sx={{ height: '100%' }}>
            <CardContent>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
                <AttachMoneyIcon color="success" fontSize="small" />
                <Typography color="text.secondary" variant="body2">Cost</Typography>
              </Box>
              <Typography variant="h5" sx={{ fontWeight: 600, color: 'success.main' }}>
                {formatCost(data.total_cost || 0)}
              </Typography>
            </CardContent>
          </Card>
        </Grid>

        {/* Tokens */}
        <Grid item xs={6} sm={3}>
          <Card sx={{ height: '100%' }}>
            <CardContent>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
                <TokenIcon color="secondary" fontSize="small" />
                <Typography color="text.secondary" variant="body2">Tokens</Typography>
              </Box>
              <Typography variant="h5" sx={{ fontWeight: 600 }}>
                {(data.total_tokens || 0).toLocaleString()}
              </Typography>
            </CardContent>
          </Card>
        </Grid>

        {/* Observations */}
        <Grid item xs={6} sm={3}>
          <Card sx={{ height: '100%' }}>
            <CardContent>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
                <VisibilityIcon color="info" fontSize="small" />
                <Typography color="text.secondary" variant="body2">Observations</Typography>
              </Box>
              <Typography variant="h5" sx={{ fontWeight: 600 }}>
                {data.observation_count || 0}
              </Typography>
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* Trace Info */}
      <Paper sx={{ p: 3 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 2 }}>Trace Information</Typography>
        <Grid container spacing={2}>
          <Grid item xs={12} md={6}>
            <Typography variant="caption" color="text.secondary">Trace ID</Typography>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <Typography
                variant="body1"
                sx={{ fontFamily: 'monospace', wordBreak: 'break-all' }}
              >
                {data.trace_id}
              </Typography>
              <Tooltip title={copied ? "Copied!" : "Copy Trace ID"}>
                <IconButton
                  size="small"
                  onClick={handleCopyTraceId}
                  color={copied ? "success" : "default"}
                >
                  {copied ? <CheckIcon fontSize="small" /> : <ContentCopyIcon fontSize="small" />}
                </IconButton>
              </Tooltip>
            </Box>
          </Grid>
          <Grid item xs={12} md={6}>
            <Typography variant="caption" color="text.secondary">Timestamp</Typography>
            <Typography variant="body1">
              {data.timestamp ? new Date(data.timestamp).toLocaleString() : 'N/A'}
            </Typography>
          </Grid>
        </Grid>
      </Paper>
    </Box>
  );
}
