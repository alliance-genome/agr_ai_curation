import React from 'react';
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Typography,
} from '@mui/material';
import type { ChangelogEntry } from '../content/changelog';

interface ChangelogDialogProps {
  open: boolean;
  entry?: ChangelogEntry;
  onClose: () => void;
  onViewAll: () => void;
}

const ChangelogDialog: React.FC<ChangelogDialogProps> = ({ open, entry, onClose, onViewAll }) => {
  if (!entry) {
    return null;
  }

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>
        What&apos;s New: v{entry.version}
        <Typography variant="body2" color="text.secondary">
          {entry.date}
        </Typography>
      </DialogTitle>
      <DialogContent dividers>
        <Typography variant="h6" sx={{ mb: 2 }}>
          {entry.title}
        </Typography>

        {entry.sections.map((section) => (
          <Box key={section.heading} sx={{ mb: 2 }}>
            <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
              {section.heading}
            </Typography>
            {section.text && (
              <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                {section.text}
              </Typography>
            )}
            {section.bullets && section.bullets.length > 0 && (
              <Box component="ul" sx={{ mt: 1, mb: 0, pl: 2 }}>
                {section.bullets.map((bullet) => (
                  <Typography component="li" key={bullet} variant="body2" sx={{ mb: 0.5 }}>
                    {bullet}
                  </Typography>
                ))}
              </Box>
            )}
          </Box>
        ))}
      </DialogContent>
      <DialogActions>
        <Button onClick={onViewAll} variant="outlined">
          View Full Changelog
        </Button>
        <Button onClick={onClose} variant="contained">
          Close
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default ChangelogDialog;
