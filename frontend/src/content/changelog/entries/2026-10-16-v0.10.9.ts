import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-10-16-v0.10.9',
  version: '0.10.9',
  date: 'October 16, 2026',
  title: 'More Reliable PDF Reading and Longer Curation Runs',
  sections: [
    {
      heading: 'Clear PDF Reading Errors',
      bullets: [
        'If a required part of a PDF cannot be read, the entire flow now stops with a clear error. An incomplete extraction is not reported as successful.',
        'Agents now select numbered passages from the current document, reducing errors caused by copying long source identifiers. Evidence remains checked against the original document text.',
      ],
    },
    {
      heading: 'Room for Longer Tasks',
      bullets: [
        'Chat and AI Studio now use shared automatic context compaction instead of discarding tool results based on their age or an arbitrary text limit. Their conversation histories remain separate.',
        'Complete selected document sections and tool results remain available as work continues. Larger incoming results are handled through verified source ranges when needed to fit the model.',
        'Higher turn limits give longer extraction and Studio tasks more room to finish. Failed runs from before this update are not rerun automatically.',
      ],
    },
  ],
};

export default entry;
