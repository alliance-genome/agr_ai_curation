import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-09-08-v0.9.8',
  version: '0.9.8',
  date: 'September 8, 2026',
  title: 'Flow verification and validation summaries',
  releaseUrl: 'https://github.com/alliance-genome/agr_ai_curation/releases/tag/v0.9.8',
  sections: [
    {
      heading: 'Fixes',
      bullets: [
        'AI Chat can review flow connections and export settings with fewer calls, reducing the chance of stopping before verification finishes.',
        'Final summaries now receive completed automatic validation results, including resolved identifiers and any rejected updates.',
        'Fixed a problem that could prevent Unload PDF from working after switching between browser tabs.',
        'AI Chat can inspect long custom-agent instructions and their saved platform rules during flow review.',
        'When an extractor change affects file output, guidance now points to Choose output fields and explains how to save the updated layout.',
        'AI Chat can inspect past runs from Flows and Agent Workshop, including the instructions actually used.',
        'Feedback confirmations now distinguish submitted notifications from reports that could not be sent.',
        'Improved diagnostic links for AI Chat so failed conversations are easier to investigate.',
      ],
    },
  ],
};

export default entry;
