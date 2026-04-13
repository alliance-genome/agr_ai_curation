import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-04-13-v0.5.6',
  version: '0.5.6',
  date: 'April 13, 2026',
  title: 'Curation Evidence Resolution Hotfix',
  sections: [
    {
      heading: 'Curation Evidence Highlighting',
      text: 'This hotfix repairs curation review evidence highlighting so saved workspace evidence is resolved against the real PDF document before it is persisted.',
      bullets: [
        'New curation sessions now ground evidence anchors against PDFX chunks during workspace bootstrap, matching the stronger document-backed path used by manual evidence recompute.',
        'Historical prep rows that do not carry a stored user ID now fall back to the current user during evidence resolution, which avoids silently skipping chunk lookup.',
        'Added regression coverage for the `c rb 8F105` quote shape from the sample fly paper, for current-user fallback, and for repeated document-resolution caching.',
      ],
    },
  ],
};

export default entry;
