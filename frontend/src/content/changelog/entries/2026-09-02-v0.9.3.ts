import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-09-02-v0.9.3',
  version: '0.9.3',
  date: 'September 2, 2026',
  title: 'Redesigned Agent Studio and Chat History',
  sections: [
    {
      heading: 'Chat History',
      bullets: [
        'Chat History now uses compact conversation rows that expand in place, with search, selection actions, and clearer Assistant, Studio, and document context.',
        'Open a transcript without leaving the history list, then resume its chat or Studio session from the expanded row.',
      ],
    },
    {
      heading: 'Agent Studio',
      bullets: [
        'Agent Studio now gives the work area more room and lets you collapse or reopen Claude with the rail button or Ctrl+. (Cmd+. on macOS).',
        'Agent guides now explain when to use or avoid each agent and organize envelope fields, validators, provenance, tools, and prompts in focused tabs.',
        'Validation-agent guides now explain whether checks run automatically and where to clone an agent when you need different behavior.',
        'Agent Workshop now keeps setup, prompt, tools, and versions in one editing workflow, identifies tools that require a document, and asks before you leave with unapplied changes.',
      ],
    },
    {
      heading: 'Flow Builder',
      bullets: [
        'Selecting a step now opens a resizable panel beside the canvas, or an in-builder drawer on narrow screens, so the flow remains visible while you edit.',
        'Automatic checks are summarized with individual switches and detail popovers when a check can be turned off; blocking and locked checks remain summarized separately.',
        'Apply or cancel step edits explicitly; if you select another step or hide a panel with unapplied edits, choose Apply, Discard, or Keep editing.',
        'Links from a step open the related agent guide or envelope, and returning to Flows keeps the graph intact.',
      ],
    },
  ],
};

export default entry;
