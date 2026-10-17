import React, { useContext } from 'react';
import { Box, Typography, Badge } from '@mui/material';
import { Inventory2 as BatchIcon } from '@mui/icons-material';
import { useNavigate } from 'react-router-dom';

import { BatchCountsContext } from '../features/notifications/useGlobalPolling';

const BatchNavIcon: React.FC = () => {
  const navigate = useNavigate();
  const status = useContext(BatchCountsContext);
  const pendingDocuments = status === null ? 0 : status.pending_documents;

  const handleClick = () => {
    navigate('/batch');
  };

  return (
    <Box
      // CR-12: Add accessibility attributes
      role="button"
      tabIndex={0}
      aria-label={`Batch processing${pendingDocuments > 0 ? `, ${pendingDocuments} document${pendingDocuments === 1 ? '' : 's'} remaining` : ''}`}
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
        badgeContent={pendingDocuments}
        color="warning"
        invisible={pendingDocuments === 0}
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
