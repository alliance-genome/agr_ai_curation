import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-09-08-v0.9.7',
  version: '0.9.7',
  date: 'September 8, 2026',
  title: 'Evidence and validation review fixes',
  releaseUrl: 'https://github.com/alliance-genome/agr_ai_curation/releases/tag/v0.9.7',
  sections: [
    {
      heading: 'Fixes',
      bullets: [
        'Fixed an evidence-linking problem that could stop allele extraction before results were saved.',
        'Chat output now receives possible database matches and their connection to the extracted field, so it can explain unresolved validation results.',
        'Restored access to older uploaded PDFs whose files were missing from the active storage directory.',
      ],
    },
  ],
};

export default entry;
