import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-04-11-v0.5.0',
  version: '0.5.0',
  date: 'April 11, 2026',
  title: 'Curation Workspace, Evidence Pipeline & Submission',
  sections: [
    {
      heading: 'Curation Workspace',
      text: 'A new review and curation workspace for side-by-side paper review with inline annotation editing.',
      bullets: [
        'Candidate queue, evidence panel, and annotation editor with PDF highlighting.',
        'Accept, edit, reject, and reset controls for individual annotations with full decision logging.',
        'Autosave and session hydration so in-progress curation sessions survive page reloads.',
        'Inventory dashboard with stats cards, saved filter views, and batch flow-run grouping.',
      ],
    },
    {
      heading: 'Evidence System',
      bullets: [
        'New record_evidence tool that extracts and verifies quoted passages from source PDFs with fuzzy matching.',
        'Evidence highlighting in the PDF viewer with bidirectional linking between annotations and source text.',
        'Evidence accumulation across flow steps, visible in audit logs, chat, and export endpoints.',
        'Rolled out evidence recording to all extractor agents with domain-specific guidance.',
      ],
    },
    {
      heading: 'Submission Pipeline',
      bullets: [
        'External submission adapter interface for sending curated data to MOD-specific endpoints.',
        'Submission preview lets curators inspect exactly what will be submitted before confirming.',
        'Retry and submission history tracking with end-to-end test coverage.',
        'Field-level validation badges show which annotations need attention before submission.',
      ],
    },
    {
      heading: 'Curation Prep Agent',
      bullets: [
        'New Curation Prep Agent that automatically organizes extraction results into a review-ready format.',
        'Supervisor-triggered handoff delegates to the prep agent when extraction is complete.',
        'Runs as a composable step within multi-stage flows.',
      ],
    },
    {
      heading: 'Centralized Logging',
      bullets: [
        'Replaced file-based log storage with Loki-backed centralized logging for faster, more reliable queries.',
        'AI agent log tools now query Loki directly instead of Docker CLI.',
      ],
    },
    {
      heading: 'Agent Studio',
      bullets: [
        'Read-only code inspection tools let you view agent source code and prompt templates without leaving Agent Studio.',
        'Friendly error messages for API failures instead of raw error text.',
      ],
    },
    {
      heading: 'Flow Builder',
      bullets: [
        'Filename template variables let you include paper title, group name, or date in formatter output filenames.',
        'Prompt version selectors on flow nodes so you can pin a specific prompt version per step.',
        'Evidence preview counts on flow nodes show how many evidence records each step produced.',
      ],
    },
    {
      heading: 'Security',
      bullets: [
        'Hardened all containers after a supply-chain incident: removed Docker CLI from backend image, locked down network access.',
        'Added SHA-256 verification to microVM builds and baked runtime packages to eliminate unsigned downloads at boot.',
      ],
    },
    {
      heading: 'Bug Fixes',
      bullets: [
        'Fixed suggestion submission silently succeeding when the SNS publish to the Alliance ingest queue failed.',
        'Fixed CSV formatter dropping files during batch runs and not propagating batch failure status.',
        'Fixed MOD dropdown using the wrong source for custom prompt group overrides.',
        'Fixed logout flow: cookies clear properly, auto-login after logout is prevented, redirect works correctly.',
        'Fixed PDF element normalization to preserve inline formatting in extracted markdown.',
      ],
    },
  ],
};

export default entry;
