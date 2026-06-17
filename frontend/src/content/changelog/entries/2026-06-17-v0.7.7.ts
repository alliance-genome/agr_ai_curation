import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-06-17-v0.7.7',
  version: '0.7.7',
  date: 'June 17, 2026',
  title: 'Generic PDF Export and Repeat Save Fixes',
  sections: [
    {
      heading: 'Generic PDF Exports',
      bullets: [
        'Generic PDF extraction now stores flexible keyed objects, so CSV, TSV, and JSON exports can use real object attributes instead of trying to rebuild columns from narrative text.',
        'Curators can ask for tables such as tumor classification rows and get exportable fields like species, tumor type, section, and extracted phrase when those attributes were captured during extraction.',
        'Generic claim text is no longer split on punctuation to invent export columns, so semicolons and narrative evidence stay intact.',
      ],
    },
    {
      heading: 'More Reliable File Saves',
      bullets: [
        'Repeating the same formatter export in a chat now updates the existing file entry for that session, format, and filename intent instead of creating a duplicate file row each time.',
        'When a repeated export is refreshed, the old per-trace file is cleaned up so the download link and stored file stay aligned.',
      ],
    },
    {
      heading: 'Clearer Extraction Data',
      bullets: [
        'Internal extraction payloads now use clearer `extracted_objects` naming so they are easier to distinguish from post-validation curation objects.',
        'Formatter tools now inspect generic object attributes directly when choosing export columns.',
      ],
    },
    {
      heading: 'Under The Hood',
      bullets: [
        'Added generic object attribute validation, attribute consistency notices for extraction, formatter projection support, and regression coverage for CSV/TSV/JSON exports.',
        'Updated integration fixtures and file-output identity tests for the generic object and repeat-export paths.',
      ],
    },
  ],
};

export default entry;
