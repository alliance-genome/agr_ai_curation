import React, { useEffect, useState } from 'react';
import {
  Box,
  Paper,
  Typography,
  Grid,
  Card,
  CardContent,
  LinearProgress,
  Alert,
} from '@mui/material';
import {
  Storage,
  Description,
  CloudSync,
  Check,
} from '@mui/icons-material';

interface HealthData {
  status: string;
  checks: {
    api: string;
    weaviate: string;
  };
  details?: {
    weaviate?: {
      version: string;
      nodes: number;
      collections: number;
    };
  };
}

interface DocumentStats {
  pagination?: {
    total_items: number;
  };
}

const Dashboard: React.FC = () => {
  const [healthData, setHealthData] = useState<HealthData | null>(null);
  const [documentStats, setDocumentStats] = useState<DocumentStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchData = async () => {
      setLoading(true);
      setError(null);

      try {
        // Fetch health data
        const healthResponse = await fetch('/api/weaviate/health');
        const health = await healthResponse.json();
        setHealthData(health);

        // Fetch document stats
        const docsResponse = await fetch('/api/weaviate/documents?page=1&page_size=1');
        const docs = await docsResponse.json();
        setDocumentStats(docs);
      } catch (err) {
        console.error('Error fetching dashboard data:', err);
        setError('Failed to load dashboard data');
      } finally {
        setLoading(false);
      }
    };

    fetchData();
    // Refresh every 30 seconds
    const interval = setInterval(fetchData, 30000);
    return () => clearInterval(interval);
  }, []);

  const stats = {
    totalDocuments: documentStats?.pagination?.total_items || 0,
    totalVectors: healthData?.details?.weaviate?.collections || 0,
    totalChunks: 0, // This would need a specific endpoint
    processingDocuments: 0,
  };

  return (
    <Box>
      <Typography variant="h4" gutterBottom>
        Weaviate Dashboard
      </Typography>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {loading && <LinearProgress sx={{ mb: 2 }} />}

      <Grid container spacing={3} sx={{ mt: 1 }}>
        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Box sx={{ display: 'flex', alignItems: 'center', mb: 2 }}>
                <Description sx={{ mr: 1, color: 'primary.main' }} />
                <Typography color="text.secondary" variant="body2">
                  Total Documents
                </Typography>
              </Box>
              <Typography variant="h4">
                {stats.totalDocuments}
              </Typography>
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Box sx={{ display: 'flex', alignItems: 'center', mb: 2 }}>
                <CloudSync sx={{ mr: 1, color: 'primary.main' }} />
                <Typography color="text.secondary" variant="body2">
                  Total Vectors
                </Typography>
              </Box>
              <Typography variant="h4">
                {stats.totalVectors}
              </Typography>
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Box sx={{ display: 'flex', alignItems: 'center', mb: 2 }}>
                <Storage sx={{ mr: 1, color: 'primary.main' }} />
                <Typography color="text.secondary" variant="body2">
                  Total Chunks
                </Typography>
              </Box>
              <Typography variant="h4">
                {stats.totalChunks}
              </Typography>
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Box sx={{ display: 'flex', alignItems: 'center', mb: 2 }}>
                <Check sx={{ mr: 1, color: 'success.main' }} />
                <Typography color="text.secondary" variant="body2">
                  Processing
                </Typography>
              </Box>
              <Typography variant="h4">
                {stats.processingDocuments}
              </Typography>
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12}>
          <Paper sx={{ p: 3 }}>
            <Typography variant="h6" gutterBottom>
              System Status
            </Typography>
            <Box sx={{ mt: 2 }}>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 1 }}>
                <Typography variant="body2">Database Connection</Typography>
                <Typography
                  variant="body2"
                  color={healthData?.checks?.weaviate === 'healthy' ? 'success.main' : 'error.main'}
                >
                  {healthData?.checks?.weaviate === 'healthy' ? 'Connected' : 'Disconnected'}
                </Typography>
              </Box>
              <LinearProgress
                variant="determinate"
                value={healthData?.checks?.weaviate === 'healthy' ? 100 : 0}
                color={healthData?.checks?.weaviate === 'healthy' ? 'success' : 'error'}
              />
            </Box>
            <Box sx={{ mt: 3 }}>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 1 }}>
                <Typography variant="body2">API Service</Typography>
                <Typography
                  variant="body2"
                  color={healthData?.checks?.api === 'healthy' ? 'success.main' : 'error.main'}
                >
                  {healthData?.checks?.api === 'healthy' ? 'Active' : 'Inactive'}
                </Typography>
              </Box>
              <LinearProgress
                variant="determinate"
                value={healthData?.checks?.api === 'healthy' ? 100 : 0}
                color={healthData?.checks?.api === 'healthy' ? 'success' : 'error'}
              />
            </Box>
            {healthData?.details?.weaviate?.version && (
              <Box sx={{ mt: 3 }}>
                <Typography variant="body2" color="text.secondary">
                  Weaviate Version: {healthData.details.weaviate.version}
                </Typography>
              </Box>
            )}
          </Paper>
        </Grid>
      </Grid>
    </Box>
  );
};

export default Dashboard;