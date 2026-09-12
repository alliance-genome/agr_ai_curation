import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-09-09-v0.9.9',
  version: '0.9.9',
  date: 'September 9, 2026',
  title: 'Keep your most-used flows close at hand',
  releaseUrl: 'https://github.com/alliance-genome/agr_ai_curation/releases/tag/v0.9.9',
  sections: [
    {
      heading: 'Your flow list',
      bullets: [
        'Choose which saved flows appear beside chat with Add flow. Search the list to find a flow or bring back one you have hidden.',
        'Drag the dotted handle to reorder your flow cards, or use Move up and Move down in the card menu. Your choices are saved for your account.',
        'Hide removes a flow from this list without deleting it. You can still edit or delete saved flows in the Flows workspace.',
      ],
    },
    {
      heading: 'Fixes included in this update',
      bullets: [
        'Includes the flow verification, validation summary, PDF unloading and saved-result inspection fixes from 0.9.8.',
        'Interrupted flow runs now explain the connection problem without incorrectly reporting that saving the run failed.',
      ],
    },
  ],
};

export default entry;
