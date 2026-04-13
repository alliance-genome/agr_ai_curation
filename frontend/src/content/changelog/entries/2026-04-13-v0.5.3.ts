import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-04-13-v0.5.3',
  version: '0.5.3',
  date: 'April 13, 2026',
  title: 'Release Hardening & Dev Validation',
  sections: [
    {
      heading: 'Retrieval & Runtime Stability',
      text: 'Supersedes the 0.5.2 candidate after hardening the live retrieval and runtime paths used during dev release validation.',
      bullets: [
        'Moved post-search reranking onto Amazon Bedrock Cohere Rerank 3.5 after Weaviate-native rerank proved unreliable on the live stack.',
        'Removed legacy API-layer chat model overrides so ordinary UI requests now respect the configured runtime defaults again.',
        'Improved backend handling for document cleanup and runtime context propagation used by batch/package-backed tools.',
      ],
    },
    {
      heading: 'Validation Coverage',
      bullets: [
        'Expanded the dev release smoke to cover upload, artifacts, chat, streaming chat, curation workspace bootstrap, custom flows, evidence export, batch execution, and ZIP output validation.',
        'Added regression coverage for Bedrock reranking, chat stream handling, stale-document cleanup, and package-runner request context hydration.',
        'Re-validated the dev candidate with green backend unit, contract, integration, and deep smoke runs before cutting this patch release.',
      ],
    },
    {
      heading: 'Curator Experience',
      bullets: [
        'Keeps the in-app What’s New popup anchored to the substantive 0.5.0 release notes while newer patch releases focus on stabilization.',
        'Preserves the major curator-facing 0.5.0 highlights in the popup instead of replacing them with thin patch-note dialogs.',
      ],
    },
  ],
};

export default entry;
