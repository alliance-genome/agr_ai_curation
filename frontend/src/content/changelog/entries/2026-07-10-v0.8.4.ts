import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-07-10-v0.8.4',
  version: '0.8.4',
  date: 'July 10, 2026',
  title: 'Documents and Export Reliability',
  sections: [
    {
      heading: 'Documents Library',
      bullets: [
        'Filename search now covers your complete document library, including files beyond the first page.',
        'Pagination, filtering, and supported sorting now use the full server-side result set.',
        'Duplicate-upload messages direct you to find the existing file in Documents.',
      ],
    },
    {
      heading: 'CSV, TSV, and JSON Exports',
      bullets: [
        'Exports now use extraction results from the active work session and loaded paper.',
        'A normal export selects the latest result; combining multiple results requires an explicit selection.',
        'Older sessions for the same paper can no longer interfere with a new export.',
      ],
    },
  ],
};

export default entry;
