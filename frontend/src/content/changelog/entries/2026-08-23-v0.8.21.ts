import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-08-23-v0.8.21',
  version: '0.8.21',
  date: 'August 23, 2026',
  title: 'PDF Page Accuracy Hotfix',
  sections: [
    {
      heading: 'PDF Evidence and Search',
      bullets: [
        'Search results and evidence from newly extracted PDFs now retain their correct source page numbers.',
        'Back-matter sections such as figure legends, funding, and references no longer silently appear on page 1 when PDFX supplies page provenance.',
      ],
    },
    {
      heading: 'Under The Hood',
      bullets: [
        'Added strict validation of PDFX page-provenance metadata before page numbers are stored in processed elements and Weaviate chunks.',
        'Kept existing provider-Markdown behavior unchanged when the new PDFX sidecar is not part of the extraction response.',
      ],
    },
  ],
};

export default entry;
