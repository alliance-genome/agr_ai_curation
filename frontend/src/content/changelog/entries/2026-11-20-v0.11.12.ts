import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  "id": "2026-11-20-v0.11.12",
  "version": "0.11.12",
  "date": "November 20, 2026",
  "title": "Updated AI libraries behind the site",
  "releaseUrl": "https://agr-jira.atlassian.net/projects/KANBAN/versions/11007",
  "sections": [
    {
      "heading": "Under the Hood",
      "bullets": [
        "The software libraries the site uses to talk to the AI service, and to look up papers in the Alliance literature collection, were updated to their current versions. Nothing about how you use the site changes, and there is nothing you need to do.",
        "If anything looks different from before, or a run stops with an error you have not seen before, please report it using the feedback menu so we can look at it."
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
