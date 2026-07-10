import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-07-10-v0.8.7',
  version: '0.8.7',
  date: 'July 10, 2026',
  title: 'Review and Evidence Navigation Fixes',
  sections: [
    {
      heading: 'Curation Review',
      bullets: [
        'Review & Curate can now prepare completed flow extraction results instead of incorrectly reporting that the originating session has no candidates.',
        'Evidence links now recover the correct PDF text highlight when document formatting causes PDF.js to select the wrong native occurrence.',
      ],
    },
  ],
};

export default entry;
