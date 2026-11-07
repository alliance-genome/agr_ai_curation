import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  "id": "2026-11-07-v0.11.5",
  "version": "0.11.5",
  "date": "November 7, 2026",
  "title": "Chat counts that match your results",
  "releaseUrl": "https://agr-jira.atlassian.net/projects/KANBAN/versions/10965",
  "sections": [
    {
      "heading": "Fixes",
      "bullets": [
        "When the supervisor removes a saved record that does not answer your question, chat now names that record and the supervisor's reason at the end of the review, and says how many records remain. Before, the final answer could still count and list a record that had just been removed, even though your saved results were already correct."
      ]
    },
    {
      "heading": "Easier to Follow",
      "bullets": [
        "Agent Studio chat can explain this line, including who removed the record and how to bring it back with Restore for review."
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
