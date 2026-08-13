import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-07-10-v0.8.5',
  version: '0.8.5',
  date: 'July 10, 2026',
  title: 'ABC Literature PDF Selection',
  sections: [
    {
      heading: 'Add Literature',
      bullets: [
        'When ABC Literature has both a MOD-specific final main PDF and a shared PMC main PDF, imports now automatically select the PDF for your MOD.',
        'Shared PMC PDFs remain the fallback, while genuine multi-MOD ties still require curator selection.',
        'Canonical converted article text remains independent of the PDF chosen for viewing.',
      ],
    },
  ],
};

export default entry;
