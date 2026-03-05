import React from 'react';
import { Box, Paper, Typography } from '@mui/material';
import { CHANGELOG_ENTRIES } from '../content/changelog';

const ChangelogPage: React.FC = () => {
  return (
    <Box sx={{ p: 3, width: '100%', overflowY: 'auto' }}>
      <Typography variant="h4" sx={{ mb: 1 }}>
        Changelog
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        Product updates and release notes.
      </Typography>

      {CHANGELOG_ENTRIES.map((entry) => (
        <Paper key={entry.id} elevation={1} sx={{ p: 2.5, mb: 2 }}>
          <Typography variant="h6">
            {entry.title} v{entry.version}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            {entry.date}
          </Typography>

          {entry.sections.map((section) => (
            <Box key={section.heading} sx={{ mb: 2 }}>
              <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
                {section.heading}
              </Typography>
              {section.text && (
                <Typography variant="body2" sx={{ mt: 0.5 }}>
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
        </Paper>
      ))}
    </Box>
  );
};

export default ChangelogPage;
