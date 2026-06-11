import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-06-11-v0.7.4',
  version: '0.7.4',
  date: 'June 11, 2026',
  title: 'Reliability Fixes for Data-Rich Papers',
  sections: [
    {
      heading: 'Flows That Finish',
      bullets: [
        'Flows that extract many records from a single paper now run to completion reliably, instead of failing partway through on the largest papers.',
        'When a validator cannot finish checking every record on a very data-rich paper, the flow now flags those records for your review and still finishes, instead of failing the whole run — so you keep the rest of the extraction.',
        'Validation on papers with many experimental conditions now completes instead of timing out.',
      ],
    },
    {
      heading: 'Smarter, Calmer Lookups',
      bullets: [
        'Allele lookups now stay within the paper\'s species when it is known, so you no longer see a similar-looking allele matched from the wrong organism.',
        'When a specialist cannot resolve something, the assistant now reports "not found" or "unresolved" plainly and stops, instead of re-running the same lookup over and over.',
        'Allele symbol lookups against the database are substantially faster.',
      ],
    },
    {
      heading: 'Under The Hood',
      bullets: [
        'Hardened how extraction and validation specialists return their results so a model can no longer deliver output as a stray text message, and a struggling validator can no longer stall an otherwise-good run.',
        'Operational settings (turn budgets, batch sizes, timeouts, and caps) are now documented and configurable, so behavior can be tuned without a code change.',
      ],
    },
  ],
};

export default entry;
