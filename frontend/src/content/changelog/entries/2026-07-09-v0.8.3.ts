import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-07-09-v0.8.3',
  version: '0.8.3',
  date: 'July 9, 2026',
  title: 'AI Model Upgrade (GPT-5.6)',
  sections: [
    {
      heading: 'New AI Models',
      text: "The curation agents now run on OpenAI's newer GPT-5.6 models for stronger extraction and validation.",
      bullets: [
        'Extraction, routing, and output agents now use GPT-5.6 Sol.',
        'Validation and utility agents now use GPT-5.6 Terra.',
        'Custom agents were moved to the matching GPT-5.6 model automatically, and your reasoning-effort settings were preserved.',
      ],
    },
  ],
};

export default entry;
