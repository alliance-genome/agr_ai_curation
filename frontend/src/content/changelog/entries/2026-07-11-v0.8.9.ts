import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-07-11-v0.8.9',
  version: '0.8.9',
  date: 'July 11, 2026',
  title: 'Allele Validation Materialization Hotfix',
  sections: [
    {
      heading: 'Allele Curation',
      bullets: [
        'Resolved allele identifiers, symbols, and taxa now materialize into validated Allele review records even when the validator returns raw database facts instead of an internal envelope wrapper.',
        'Allele flow results no longer report resolved identity fields as unmapped or omit the corresponding Review & Curate allele row.',
      ],
    },
  ],
};

export default entry;
