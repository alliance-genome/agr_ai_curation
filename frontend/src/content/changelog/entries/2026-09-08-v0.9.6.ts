import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-09-08-v0.9.6',
  version: '0.9.6',
  date: 'September 8, 2026',
  title: 'Custom-field validation fixes',
  releaseUrl: 'https://github.com/alliance-genome/agr_ai_curation/releases/tag/v0.9.6',
  sections: [
    {
      heading: 'Fixes',
      bullets: [
        'Fixed a problem that could leave validated allele fields blank in custom extraction results.',
        'Validators now return values through the selected custom fields, while ambiguous identities remain unresolved for review.',
        'Chat output now follows its formatting instructions when presenting structured extraction results.',
        'Saved agents and flows using GPT-5.6 Sol with medium reasoning now use Astra with low reasoning. Their prompts, tools, and output structures are preserved.',
      ],
    },
  ],
};

export default entry;
