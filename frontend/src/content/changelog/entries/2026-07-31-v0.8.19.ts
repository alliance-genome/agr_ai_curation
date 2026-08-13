import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-07-31-v0.8.19',
  version: '0.8.19',
  date: 'July 31, 2026',
  title: 'Supplement Upload Processing Hotfix',
  sections: [
    {
      heading: 'Documents',
      bullets: [
        'Manually uploaded supplement PDFs are now converted from the exact file uploaded, even when the file matches an ABC Literature reference.',
        'Reference-style filenames such as FBrf identifiers are safe to use for manual uploads and do not cause the main article text to replace the supplement.',
      ],
    },
    {
      heading: 'Under The Hood',
      bullets: [
        'Checksum matches can identify supplement provenance without reusing reference-level merged Markdown for the uploaded document.',
      ],
    },
  ],
};

export default entry;
