import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-07-10-v0.8.6',
  version: '0.8.6',
  date: 'July 10, 2026',
  title: 'PDF Viewer Access Restored',
  sections: [
    {
      heading: 'Documents and Chat',
      bullets: [
        'PDFs that finish processing and appear in the Documents Library can once again be loaded into chat and opened in the PDF viewer.',
        'Production deployment checks now verify the public PDF route directly so a stale maintenance route cannot silently block documents.',
      ],
    },
  ],
};

export default entry;
