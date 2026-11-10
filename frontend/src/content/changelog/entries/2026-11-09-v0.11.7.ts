import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  "id": "2026-11-09-v0.11.7",
  "version": "0.11.7",
  "date": "November 9, 2026",
  "title": "Your edits to validated entries stay put",
  "releaseUrl": "https://agr-jira.atlassian.net/projects/KANBAN/versions/10967",
  "sections": [
    {
      "heading": "Fixes",
      "bullets": [
        "After you edit a validated entry, such as an allele's symbol, later review saves now work. Before, restoring a retired record, or letting the supervisor remove or re-check a record, stopped review with a system error every time.",
        "Your edit stays in place when review checks the entry again and finds the same allele or gene. If review would replace it with a different one, review pauses and asks you to use Revalidate on that record, so your edit is never replaced without your say.",
        "The supervisor can no longer remove a record whose validated entry you edited."
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
