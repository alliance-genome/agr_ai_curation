import React, { useEffect, useState } from 'react';
import { Box, IconButton, Typography, Badge } from '@mui/material';
import { Inventory2 as BatchIcon } from '@mui/icons-material';
import { useNavigate } from 'react-router-dom';

interface BatchStatus {
  runningCount: number;
  pendingDocuments: number;
}

const BatchNavIcon: React.FC = () => {
  const navigate = useNavigate();
  const [status, setStatus] = useState<BatchStatus>({ runningCount: 0, pendingDocuments: 0 });

  // Poll for running batches every 10 seconds using lightweight endpoint
  useEffect(() => {
    const checkRunningBatches = async () => {
      try {
        const response = await fetch('/api/batches/running-count', {
          credentials: 'include',
        });
        if (response.ok) {
          const data = await response.json();
          setStatus({
            runningCount: data.running_count || 0,
            pendingDocuments: data.pending_documents || 0,
          });
        }
      } catch (error) {
        // Silently fail - badge just won't show
      }
    };

    // Initial check
    checkRunningBatches();

    // Poll every 10 seconds
    const interval = setInterval(checkRunningBatches, 10000);

    return () => clearInterval(interval);
  }, []);

  const handleClick = () => {
    navigate('/batch');
  };

  return (
    <Box
      // CR-12: Add accessibility attributes
      role="button"
      tabIndex={0}
      aria-label={`Batch processing${status.pendingDocuments > 0 ? `, ${status.pendingDocuments} document${status.pendingDocuments === 1 ? '' : 's'} remaining` : ''}`}
      onKeyDown={(e) => e.key === 'Enter' && handleClick()}
      sx={{
        display: 'flex',
        alignItems: 'center',
        gap: 0.5,
        cursor: 'pointer',
        textDecoration: 'none',
        color: 'inherit',
        marginRight: 2,
        '&:hover': {
          opacity: 0.8,
        },
      }}
      onClick={handleClick}
    >
      <Badge
        badgeContent={status.pendingDocuments}
        color="warning"
        invisible={status.pendingDocuments === 0}
        sx={{
          '& .MuiBadge-badge': {
            fontSize: '0.65rem',
            height: 16,
            minWidth: 16,
          },
        }}
      >
        <BatchIcon fontSize="small" />
      </Badge>
      <Typography variant="body2">Batch</Typography>
    </Box>
  );
};

export default BatchNavIcon;
