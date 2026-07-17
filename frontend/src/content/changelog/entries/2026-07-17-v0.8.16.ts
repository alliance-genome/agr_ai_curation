import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-07-17-v0.8.16',
  version: '0.8.16',
  date: 'July 17, 2026',
  title: 'Batch ZIP Filename Hotfix',
  sections: [
    {
      heading: 'Batch Downloads',
      bullets: [
        'Download Zip now keeps the custom output filenames defined by your flow, matching individual result downloads.',
        'If a batch contains duplicate output filenames, only the duplicates receive a simple numbered suffix so no result is overwritten.',
      ],
    },
    {
      heading: 'Under The Hood',
      bullets: [
        'Improved cross-platform archive filename safety and added regression coverage for one-paper and multi-paper batches.',
      ],
    },
  ],
};

export default entry;
