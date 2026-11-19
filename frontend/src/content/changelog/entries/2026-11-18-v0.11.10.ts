import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  "id": "2026-11-18-v0.11.10",
  "version": "0.11.10",
  "date": "November 18, 2026",
  "title": "Output field window: the pick list stays usable as you add fields",
  "releaseUrl": "https://agr-jira.atlassian.net/projects/KANBAN/versions/11004",
  "sections": [
    {
      "heading": "Fixes",
      "bullets": [
        "In the \"Choose output fields\" window for a CSV, TSV or JSON output step, the list you pick fields from got shorter each time you added a field. After several fields it disappeared, so no more fields could be chosen with the mouse. The window now scrolls as a whole and the pick list keeps its size."
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
