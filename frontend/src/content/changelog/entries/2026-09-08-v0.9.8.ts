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
        'Improved diagnostic links for AI Chat so failed conversations are easier to investigate.',
      ],
    },
  ],
};

export default entry;
