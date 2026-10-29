import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  "id": "2026-10-29-v0.11.0",
  "version": "0.11.0",
  "date": "October 29, 2026",
  "title": "Reviewable Results, Clearer History, and More Reliable Curation",
  "releaseUrl": "https://agr-jira.atlassian.net/projects/KANBAN/versions/10920",
  "sections": [
    {
      "heading": "Review Before Completion",
      "bullets": [
        "The supervisor now reviews extracted findings and validation results before completing supported chat, flow, and batch tasks. In flows and batches, it stays within the agents configured for that workflow.",
        "When validation needs more information, the supervisor can ask the extractor to revisit the paper with the earlier finding and validation feedback. Validators resolve the supplied information; extractors gather evidence from the paper.",
        "Findings that cannot be resolved can remain explicitly unresolved for curator review rather than being presented as confirmed. The supervisor can also retire a finding when the evidence does not support keeping it."
      ]
    },
    {
      "heading": "A Clearer Record of What Changed",
      "bullets": [
        "Original extracted values, validation attempts, and review decisions are retained separately so you can inspect how a result was reached.",
        "The curation workspace shows review history alongside the record. Retired findings remain in the history instead of being deleted.",
        "Your edits remain under your control. Editing a record does not automatically rerun validation. If you explicitly request revalidation, its accepted result can replace the values in the requested scope while the earlier history is preserved."
      ]
    },
    {
      "heading": "More Reliable Reading and Exports",
      "bullets": [
        "Improved handling of selected evidence and document subsections helps agents retrieve the requested text with its source references.",
        "Agents can inspect saved results consistently across supported workflows, and export feedback now makes row counts, filtering, and warnings clearer.",
        "Longer reviews can continue through automatic context compaction without a cumulative token ceiling cutting them off. Provider failures are reported as failures even if some extraction work has already been saved."
      ]
    },
    {
      "heading": "Updated Agent Studio and Smoother Navigation",
      "bullets": [
        "Agent Studio guidance now reflects the review workflow and can retrieve saved review history when helping you understand a result.",
        "Fixed a Studio conversation error that could occur after checking a prompt and then continuing the conversation.",
        "Reduced repeated background work in document lists, batch progress, and status checks."
      ]
    },
    {
      "heading": "Existing Results and Custom Workflows",
      "text": "Older results are retained. This release does not invent past validation decisions or automatically rerun earlier extractions. Some older custom agents and flows may still use incompatible tools or instructions; they are retained unchanged. If one fails, please report the agent or flow name so we can help update it."
    }
  ]
};

export default entry;
