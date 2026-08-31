import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-09-01-v0.9.0',
  version: '0.9.0',
  date: 'September 1, 2026',
  title: 'Curator Workflows, Document Reliability, and RGD GO Curation',
  releaseUrl: 'https://agr-jira.atlassian.net/projects/KANBAN/versions/10773',
  sections: [
    {
      heading: 'RGD GO Paper Curation',
      text: 'RGD curators now have a dedicated, evidence-backed workflow for reviewing GO and disease papers.',
      bullets: [
        'The new RGD-only GO and Disease Paper Review flow keeps its specialist agents and recipes limited to the appropriate curator group.',
        'GO annotation review includes source-backed existing-annotation lookup, mature-miRNA and RNA gene-product resolution, and RGD evidence-policy validation.',
        'Agent Studio clearly exposes which agents and flow recipes are restricted to a MOD curator group.',
      ],
    },
    {
      heading: 'Documents and Literature',
      bullets: [
        'The Documents Library has a more reliable table with persistent column visibility and layout preferences.',
        'Long and supplemental-heavy papers are accepted up to the new 300-page limit, and page provenance is preserved more consistently through extraction and review.',
        'Documents that previously became stuck in processing are reconciled to a terminal state so they can be reviewed, retried, or deleted normally.',
      ],
    },
    {
      heading: 'Flows and Curation Review',
      bullets: [
        'Flow failures now fail closed instead of exposing stale success output, while cancellation and recovery behavior is safer for long-running work.',
        'Extraction results and evidence are persisted more consistently before the Review & Curate workspace opens.',
        'Custom-agent validation attachments are preserved through flow editing, validation, and execution.',
      ],
    },
    {
      heading: 'Reliability and Observability',
      bullets: [
        'Chat answers recover more reliably after idle browser connections, and agent workflows manage long-running model connections more cleanly.',
        'Expanded Sentry, Langfuse, and TraceReview coverage makes failed PDF, chat, flow, and specialist work easier to diagnose without exposing curator content.',
        'Release testing now includes real-paper evidence review and AI-driven browser journeys across document, chat, flow, and Agent Studio workflows.',
      ],
    },
  ],
};

export default entry;
