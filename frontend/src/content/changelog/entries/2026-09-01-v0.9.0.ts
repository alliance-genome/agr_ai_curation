import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-09-01-v0.9.0',
  version: '0.9.0',
  date: 'September 1, 2026',
  title: 'A New Curation Review Workspace, Faster Literature Search, and RGD GO Curation',
  releaseUrl: 'https://agr-jira.atlassian.net/projects/KANBAN/versions/10773',
  sections: [
    {
      heading: 'A New Review & Curate Workspace',
      text: 'The redesigned Review & Curate workspace puts the fields that require curator decisions into a compact, side-by-side grid.',
      bullets: [
        'Canonical curated values are shown first, while original extracted values, supporting evidence, and validator explanations remain available in the details view.',
        'Differences between extractor proposals and validated results are highlighted for curator attention instead of appearing as duplicate peer fields.',
        'Relevant fields include evidence details and editing controls, while supporting extraction metadata remains in the record without crowding the sign-off grid.',
        'Long detail panels remain usable within the browser window, and the workspace supports both light and dark modes.',
        'Review checkmarks provide lightweight, session-local progress tracking; they do not run validators or save validator decisions in this release.',
      ],
    },
    {
      heading: 'Faster, More Accurate Literature Search',
      text: 'Literature search has been tuned using a 15-paper, 150-query retrieval benchmark.',
      bullets: [
        'Weaviate hybrid-search and reranking defaults now return more relevant evidence, particularly for short and single-term searches.',
        'A lexical-search defect affecting one-token queries was corrected.',
        'An additional ranking stage that increased latency while reducing result quality was removed, improving both speed and accuracy.',
        'Retrieval diagnostics and native Weaviate backup support were added to help verify and protect the document index.',
      ],
    },
    {
      heading: 'RGD GO and Disease Paper Review',
      text: 'RGD curators now have dedicated, group-restricted workflows for reviewing GO and disease papers.',
      bullets: [
        'New RGD-only GO and combined GO-and-disease review flows preserve the two result types separately.',
        'A new GO review structure represents gene products, GO terms, evidence codes, references, qualifiers, extensions, rationale, and unresolved conditions.',
        'The workflow can look up existing GO annotations, resolve mature-miRNA and other RNA gene products, and apply RGD evidence policy.',
        'Authorized RGD curators can choose a saved agent or flow as their preferred Chat workflow and ask evidence-grounded follow-up questions.',
        'This release supports structured review and follow-up; it does not automatically submit GO annotations.',
      ],
    },
    {
      heading: 'PDF Extraction and Document Handling',
      bullets: [
        'PDFX page provenance now follows extracted text into evidence and search results instead of silently assigning later sections to page 1.',
        'Long and supplemental-heavy papers are accepted up to the new 300-page limit.',
        'PDFX provider failures and polling timeouts are distinguished and recorded more clearly for troubleshooting.',
        'Stalled or orphaned document-processing records are reconciled so they can be reviewed, retried, renamed, or deleted normally.',
        'The Documents Library now preserves column visibility, order, width, and density preferences.',
      ],
    },
    {
      heading: 'Flows, CSV Exports, and Reliability',
      bullets: [
        'Flows that produce both a specialist result and a CSV now retain and display both outputs.',
        'Unsupported characters in extracted evidence no longer prevent successful CSV download cards from surviving refresh.',
        'Custom-agent validation attachments are preserved through flow editing and execution.',
        'Allele submission planning, disease review metadata, and phenotype data-provider export context received targeted correctness fixes.',
        'Flow failures no longer expose stale successful output, while idle chat recovery and transient model-overload handling are more reliable.',
        'Expanded Sentry, Langfuse, and TraceReview coverage makes PDF, chat, flow, and specialist failures easier to diagnose without exposing curator content.',
      ],
    },
  ],
};

export default entry;
