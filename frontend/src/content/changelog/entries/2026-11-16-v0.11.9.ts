import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  "id": "2026-11-16-v0.11.9",
  "version": "0.11.9",
  "date": "November 16, 2026",
  "title": "Flows no longer fail on papers with a hidden null character",
  "releaseUrl": "https://agr-jira.atlassian.net/projects/KANBAN/versions/11001",
  "sections": [
    {
      "heading": "Fixes",
      "bullets": [
        "Some papers carry an invisible \"null\" character in their text, most likely left over from turning the PDF into text. When a flow or chat quoted a sentence that held this character, saving the results failed, and the whole run was marked as failed after the extraction work was already done. The character is now removed when results are saved, so these runs finish and keep their results. The rest of each quoted sentence is unchanged.",
        "If a flow recently failed on a paper for no clear reason, please run it again on that paper."
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
