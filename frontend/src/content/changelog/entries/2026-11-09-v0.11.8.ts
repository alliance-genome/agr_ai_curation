import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  "id": "2026-11-09-v0.11.8",
  "version": "0.11.8",
  "date": "November 9, 2026",
  "title": "Restore works for allele mentions and quotes",
  "releaseUrl": "https://agr-jira.atlassian.net/projects/KANBAN/versions/10968",
  "sections": [
    {
      "heading": "Fixes",
      "bullets": [
        "Restore for review now works for the allele mentions and evidence quotes the supervisor removed. Before, restoring one failed with \"Restored record has no unique workspace projection\", so the paper association that uses them could not be restored either. Restore the mention and the quote first, then the association."
      ]
    },
    {
      "heading": "Your Saved Work",
      "bullets": [
        "Your saved agents, flows, custom instructions and earlier results are unchanged."
      ]
    }
  ]
};

export default entry;
