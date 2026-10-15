import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-10-15-v0.10.8',
  version: '0.10.8',
  date: 'October 15, 2026',
  title: 'More Reliable Long Extractions and Batch Results',
  sections: [
    {
      heading: 'Continuing Longer Extractions',
      bullets: [
        'Fixed a problem that could stop an extraction and ask you to split your request as its working conversation grew. Supported OpenAI agent runs now organize their working context automatically and continue the same task.',
        'Previously read evidence and tool results remain saved for exact retrieval. Final synthesis retains the original available results when it cannot use retrieval tools.',
      ],
    },
    {
      heading: 'Clearer Batch Outcomes',
      bullets: [
        'A completed extraction that explicitly returns zero objects is now shown as completed instead of failing because no download was produced.',
        'Batch results explain why there is no file. Zero extracted objects do not establish that the source contains no relevant findings; incomplete or failed runs still report their actual outcome.',
        'Previously failed batch entries have not been rerun automatically. You can rerun affected documents after this update.',
      ],
    },
    {
      heading: 'Under the Hood',
      bullets: [
        'Added regression coverage for repeated context compaction, exact evidence retrieval, final synthesis and empty batch results, with the same context settings available to benchmark execution.',
      ],
    },
  ],
};

export default entry;
