import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  "id": "2026-11-04-v0.11.2",
  "version": "0.11.2",
  "date": "November 4, 2026",
  "title": "Faster, lower-cost supervisor review",
  "releaseUrl": "https://agr-jira.atlassian.net/projects/KANBAN/versions/10962",
  "sections": [
    {
      "heading": "Supervisor review",
      "bullets": [
        "The supervisor now reads related records together and saves its decisions in groups, instead of working through one record at a time. Large papers finish much sooner.",
        "Saving each set of decisions is also much quicker.",
        "The supervisor now checks each record against the saved instructions of the extraction agent that produced it. A curator's explicit request for that run can still widen or narrow them.",
        "Every record still receives its own decision and reason. No review step has been removed.",
        "Records that need a closer look are still deferred and sent back for a focused follow-up in the paper.",
        "Your saved agents, flows, prompts and earlier results are unchanged."
      ]
    }
  ]
};

export default entry;
