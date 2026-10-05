import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-10-05-v0.10.0',
  version: '0.10.0',
  date: 'October 5, 2026',
  title: 'Shared Agents and Flows, Live Progress, and GPT-6.1 Sol',
  releaseUrl: 'https://agr-jira.atlassian.net/projects/KANBAN/versions/10839',
  sections: [
    {
      heading: 'Share Agents and Flows With Your Project',
      text: 'You can now reuse agents and flows your teammates have built, instead of recreating them yourself.',
      bullets: [
        'A new Shared Library tab in Agent Studio lists custom agents, flows, and tool requests shared with your project. Filter by Mine or Shared with project, or search by name.',
        'Owners choose who can see their work: set an agent’s Visibility to Shared with project in Workshop Setup, or use Share with project and Make private on a saved flow.',
        'Open a teammate’s agent or flow read-only, then choose Clone to Workshop or Clone to edit to make your own private copy. Only the owner can change or delete the original.',
        'Run in workspace opens a shared flow under Tools → Curation Flows so you can run it on your own paper. Sharing does not override group restrictions on the agents a flow uses.',
        'Workshop’s Tools section also shows teammates’ tool requests and their status. Their private conversations are not shown.',
      ],
    },
    {
      heading: 'See Progress While Papers and Flows Run',
      bullets: [
        'While a flow runs, chat shows a progress bar with the current step, for example “Step 2 of 4: Find expression patterns”, and the time elapsed. There is no time-remaining estimate, because each step’s length depends on the paper.',
        'If a flow fails or is stopped, the bar stays beside the error and names the step where it stopped.',
        'PDF Jobs and Documents now say what the PDF reader is doing: “Waking up the PDF reader (can take a few minutes)” after it has been idle, “Waiting for the PDF reader” when it is busy, and “Reading the PDF · 35%” as it works.',
        'The upload screen now waits up to an hour for a long paper, instead of giving up after 15 minutes. A processing error now appears straight away.',
      ],
    },
    {
      heading: 'Getting to Your Papers and Chats',
      bullets: [
        'On Add Literature, each completed paper in the PDF Jobs panel now has Load for chat and Load for curation buttons, so you no longer have to find the paper again in the document list.',
        'Resume chat now opens the whole conversation, including chats longer than 100 messages. Reopening Home also loads the full conversation. If a chat cannot be loaded completely, you see an error with Start new chat rather than a blank or partial chat.',
        'ABC Literature papers that only had older, lower-quality text now import. AI Curation asks ABC to convert the paper again. Previously these imports stalled at 35% and failed after about 10 minutes with “Provider conversion exceeded 600 seconds”; please retry any paper that failed this way.',
      ],
    },
    {
      heading: 'Extraction Results and Chat Reports',
      bullets: [
        'Gene, gene-expression, phenotype, and GO extractions that find nothing in scope now finish with an empty result instead of failing. For example, a wild-type expression flow run on a paper whose expression data come only from transgenic strains now completes. An empty result does not prove the paper has no data of that type.',
        'A chat report can now contain several tables with different columns, each built from the saved results. Previously the report step refused this after the extraction had already finished.',
        'Extractions you run directly in chat are now saved, so Review & Curate can prepare them. Previously Review & Curate could report that nothing was available.',
        'Allele searches now use clues about who made an allele and its functional effect when ranking candidates, so the right allele is less likely to be dropped from a long list of similar names.',
        'If the AI provider’s automatic safety check stops an extraction in ordinary chat, chat now says so and states that no file was produced, instead of suggesting you try again. Please report the paper with the feedback button.',
        'Paper searches now retry automatically after a brief connection drop.',
      ],
    },
    {
      heading: 'Models and Agent Workshop',
      bullets: [
        'GPT-6.1 Sol replaces GPT-6 Sol. New extraction agents and the validation agents now use GPT-6.1 Sol with medium reasoning; it offers Low, Medium, and High reasoning.',
        'Your saved agents were moved to GPT-6.1 Sol for you, and you do not need to re-save anything. Reasoning levels stay the same, except that the old highest setting (xhigh) becomes High. A GPT-6.1 Sol copy of each agent’s current version, and of any older version your flows use, appears in the agent’s version history, and those flow steps now use the copies.',
        'Flexible extraction is retired, and new agents cannot choose it. The existing Flexible agents were converted for their owners to a fixed set of fields based on what they had been extracting; owners can review the fields in the agent’s Output section.',
        'Under More field options, Synonyms / source labels lets you view and edit the other names a detail goes by in papers. These names help recognize the detail without changing its output column.',
      ],
    },
    {
      heading: 'Flow Builder',
      bullets: [
        'File → Rename Flow… renames the saved flow you have open without saving your other pending edits.',
        'A reminder above the canvas, “Unsaved changes — Save this flow to your account”, stays visible until you save. Apply updates the flow draft only; Save keeps it in your account.',
      ],
    },
    {
      heading: 'Group Access and Benchmark',
      bullets: [
        'ZFIN, RGD, and SGD curators are now matched to their group when they sign in, so they receive their group’s agent instructions and group-restricted agents and flows. Xenbase curators are now recognized as a group too.',
        'A new Benchmark link in the top navigation opens the benchmark site in a new tab.',
      ],
    },
    {
      heading: 'Under The Hood',
      bullets: [
        'The interface library was upgraded to a current version; screens should look and work as before.',
        'Many problems are now reported to the development team automatically, with curator content removed, so they can be fixed without asking you to reproduce them.',
      ],
    },
  ],
};

export default entry;
