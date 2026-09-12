import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-09-03-v0.9.4',
  version: '0.9.4',
  date: 'September 3, 2026',
  title: 'Reliable Curation Review, Saved Flows, and Feedback Diagnostics',
  releaseUrl: 'https://github.com/alliance-genome/agr_ai_curation/releases/tag/v0.9.4',
  sections: [
    {
      heading: 'Review & Curate',
      bullets: [
        'Review tables now scroll vertically within the workspace, so every extracted row remains reachable without zooming out.',
      ],
    },
    {
      heading: 'Saved Flows',
      bullets: [
        'Saved flows that referenced a retired allele-validation check now open and run again. The obsolete check is removed automatically while preserving the flow’s remaining steps and settings.',
      ],
    },
    {
      heading: 'Feedback Diagnostics',
      bullets: [
        'Feedback diagnostics can again locate and inspect the correct production run, with accurate ordering, duration, cost, and trace links.',
        'Feedback submissions retain durable trace associations so reported problems can still be investigated when external trace indexing changes.',
      ],
    },
  ],
};

export default entry;
