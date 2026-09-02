import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-09-02-v0.9.1',
  version: '0.9.1',
  date: 'September 2, 2026',
  title: 'Reliable Reagent TSV Exports and Feedback Diagnostics',
  sections: [
    {
      heading: 'TSV Exports',
      bullets: [
        'Batch and chat TSV exports now format multi-valued source identifiers as the requested source-and-identifier pairs instead of failing or exposing list syntax.',
        'When one source has multiple identifiers, each source-and-identifier pair is separated with | as requested by the flow.',
        'Ambiguous unequal lists now produce a clear formatting error rather than silently dropping or mispairing source data.',
      ],
    },
    {
      heading: 'Feedback and Trace Reliability',
      bullets: [
        'Production feedback diagnostics now use the same release-pinned TraceReview version as the main application.',
        'Trace and session exports remain compatible with the current Langfuse event storage used in production.',
      ],
    },
  ],
};

export default entry;
