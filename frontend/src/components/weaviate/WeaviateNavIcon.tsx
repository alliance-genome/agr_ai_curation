import React from 'react';
import { Box, Typography } from '@mui/material';
import { Storage } from '@mui/icons-material';
import { useNavigate } from 'react-router-dom';

const WeaviateNavIcon: React.FC = () => {
  const navigate = useNavigate();

  const handleClick = () => {
    navigate('/weaviate');
  };

  return (
    <Box
      role="button"
      tabIndex={0}
      aria-label="Documents"
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
      <Storage fontSize="small" />
      <Typography variant="body2">Documents</Typography>
    </Box>
  );
};

export default WeaviateNavIcon;