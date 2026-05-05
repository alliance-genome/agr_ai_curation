import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-05-05-v0.6.0',
  version: '0.6.0',
  date: 'May 5, 2026',
  title: 'Curation Workflow Reliability and Chat History',
  sections: [
    {
      heading: 'Evidence Reliability',
      text: 'This release tightens how AI Curation verifies evidence from papers, with several fixes based directly on curator feedback.',
      bullets: [
        'Evidence matching is stricter about avoiding nearby-but-wrong sentences, while still recovering valid quotes that differ only by citation markers or small PDF extraction formatting changes.',
        'Allele extraction now handles section-label chunk references more safely instead of losing otherwise valid evidence.',
        'MGI allele flows now receive the correct MGI group rules for MGI curator accounts.',
        'Prepared curation rows keep stronger links back to the original paper evidence for review.',
      ],
    },
    {
      heading: 'Chat History and Review Workflow',
      bullets: [
        'Chat history is now stored durably so conversations can be browsed, searched, renamed, deleted, and resumed after navigation, restarts, or deployments.',
        'Curation rows can now be deleted directly from the review table when a prepared candidate should be removed rather than edited or rejected.',
        'A light/dark theme toggle is available, with the preference saved for future sessions.',
      ],
    },
    {
      heading: 'Batch and File Outputs',
      bullets: [
        'Fixed a batch-processing issue where the second document could fail silently when a formatter skipped saving its output file.',
        'Fixed TSV formatter flow steps that extracted rows but then asked for the input data again instead of producing the TSV file.',
        'Formatter output filenames can now use the source PDF name, and flow filename templates support values such as the input filename, trace ID, and timestamp.',
      ],
    },
    {
      heading: 'Feedback and Trace Review',
      bullets: [
        'Curator feedback reports now preserve trace snapshots so debugging does not depend on manually reconstructing old Langfuse traces.',
        'TraceReview now has better health checks, session bundle export, citation diagnostics, final-response extraction, and direct feedback debug links.',
        'These changes make it easier to diagnose curator-reported issues from the feedback email and trace links.',
      ],
    },
    {
      heading: 'Bug Fixes',
      bullets: [
        'Agent Studio now shows friendlier Anthropic error messages instead of raw provider payloads.',
        'Suggestion submission no longer reports success when the notification publish step fails.',
        'Production Bedrock reranker configuration now avoids missing-profile errors.',
        'The Audit panel now displays zebrafish as D. rerio instead of Z. rerio.',
      ],
    },
    {
      heading: 'Under The Hood',
      bullets: [
        'Updated AI Curation model references from GPT-5.4 to GPT-5.5 where appropriate.',
        'Improved standalone and production Docker configuration for newer auth providers and Ubuntu 24.04 hosts.',
        'Pinned Loki and moved its readiness check outside the container so observability no longer shows a false unhealthy state.',
      ],
    },
  ],
};

export default entry;
