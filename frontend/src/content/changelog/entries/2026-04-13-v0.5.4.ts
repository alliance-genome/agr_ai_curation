import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-04-13-v0.5.4',
  version: '0.5.4',
  date: 'April 13, 2026',
  title: 'PDF Viewer Highlight Cleanup',
  sections: [
    {
      heading: 'PDF Highlighting',
      text: 'This patch removes a stale legacy highlight path that could still draw chunk-level overlay boxes in the PDF viewer.',
      bullets: [
        'Removed the old chunk overlay rendering pipeline so evidence navigation now stays on the native PDF.js highlight path.',
        'Standardized PDF highlight colors on the green palette used for the current viewer experience.',
        'Updated regression coverage to ensure legacy CHUNK_PROVENANCE events no longer trigger custom overlay rendering.',
      ],
    },
    {
      heading: 'Release Validation',
      bullets: [
        'Kept the What’s New popup anchored to the substantive 0.5.0 notes while shipping this small production hotfix separately.',
        'Retained the dev release smoke improvement that refreshes the loaded document before the dedicated streaming-chat slice.',
      ],
    },
  ],
};

export default entry;
