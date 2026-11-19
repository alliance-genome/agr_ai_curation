import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  "id": "2026-11-19-v0.11.11",
  "version": "0.11.11",
  "date": "November 19, 2026",
  "title": "Fewer runs stop with an AI service error, and papers with over 50 records can be saved",
  "releaseUrl": "https://agr-jira.atlassian.net/projects/KANBAN/versions/11006",
  "sections": [
    {
      "heading": "Fixes",
      "bullets": [
        "Flows that split a paper among several writers working in parallel sometimes stopped with the message \"The AI service could not accept this request\". This happened when the AI service turned down one of the requests. The program now sends a turned-down request once more, and if one writer's part of the paper still fails, that part is run once more while the other writers keep working. If the problem continues after that, the run still stops, now with a message that the AI service is temporarily unavailable or busy, and you can start it again later.",
        "Papers with more than 50 gene expression, allele, disease, gene or phenotype records could not be saved, so the run stopped after the extraction work was done. The limit of 50 is gone: all the records found in the paper are now saved together.",
        "If a flow or chat stopped for either of these reasons, please start it again on that paper. Runs that already stopped are not restarted or repaired automatically.",
        "A review that was started before this update cannot be continued after it. This covers a chat session that still has a result awaiting review, and restoring a set-aside record on a paper last run before the update. Please start a new chat session or a new run on that paper."
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
