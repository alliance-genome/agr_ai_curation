import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-08-28-v0.8.22',
  version: '0.8.22',
  date: 'August 28, 2026',
  title: 'Custom Flow Save Hotfix',
  sections: [
    {
      heading: 'Custom Extraction Flows',
      bullets: [
        'Custom extraction agents can now be added to new flows and saved without a validation-attachment error.',
        'Existing custom extraction flows now load the validation settings inherited from their standard agent template.',
      ],
    },
    {
      heading: 'Under The Hood',
      bullets: [
        'Flow validation now resolves inherited domain-pack metadata for custom agents while preserving user access controls.',
      ],
    },
  ],
};

export default entry;
