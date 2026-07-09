import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-07-09-v0.8.0',
  version: '0.8.0',
  date: 'July 9, 2026',
  title: 'Literature Import, Live Run Status, and Curation Fixes',
  releaseUrl: 'https://agr-jira.atlassian.net/projects/KANBAN/versions/10740',
  sections: [
    {
      heading: 'Import Papers from Literature',
      text: 'You can now bring papers into AI Curation directly from the Alliance Literature (ABC) service, in addition to uploading PDFs yourself.',
      bullets: [
        'Add a paper by PMID or ABC Literature identifier and pull in the already-converted text when the Literature service has it, instead of re-uploading a PDF.',
        'Documents now show their source provenance, so you can tell whether a paper came from a direct upload or a Literature import.',
        'The Documents view and Add Literature flow were updated to support identifier-based import alongside the existing PDF upload path.',
      ],
    },
    {
      heading: 'Live Run Status',
      bullets: [
        'A live "running" indicator now shows while a chat or flow is actively working, so you can tell at a glance when the assistant is still processing.',
        'If you navigate away while a run is in progress, a completion toast appears when it finishes, with a one-click link back to that session.',
        'Flow runs no longer stop when you switch to the Documents tab mid-run.',
      ],
    },
    {
      heading: 'Figures and Gene Expression',
      bullets: [
        'Figure metadata and legends from the source provider are now captured during upload ingestion, so more figure context is available for curation.',
        'Gene-expression extraction results now keep their figure and panel provenance.',
        'Fixed a case where gene-expression builder extractions were never saved, which had kept the curation validators from running on them.',
      ],
    },
    {
      heading: 'Documents and Review Fixes',
      bullets: [
        'The publication table column sort arrows now actually sort.',
        'The curation inventory is now scoped to you instead of showing the full inventory.',
        'Fixed a case where the Documents tab could become non-scrollable.',
        'Trimmed duplicate explanation text in curation Validation Details.',
      ],
    },
    {
      heading: 'Reliability',
      bullets: [
        'Browser storage and PDF viewer restore are hardened against running out of local storage space, so the viewer recovers instead of breaking.',
        'Flows handle PDF extraction failures and empty-result ambiguity more gracefully.',
      ],
    },
    {
      heading: 'Under The Hood',
      bullets: [
        'New Sentry-based observability across AI-agent runs (chat, flows, evidence handoff) to catch and diagnose issues faster.',
        'Reworked the OpenAI Responses connection lifecycle for multi-step flows (warm-connection reuse) and added context-budget compaction for chat and Agent Studio.',
        'Pinned the OpenAI Agents SDK behind a flow/extractor smoke suite, added save-time validation that rejects broken agents and flows, and expanded deployed dev-release smoke coverage.',
      ],
    },
  ],
};

export default entry;
