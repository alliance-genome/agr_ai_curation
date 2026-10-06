import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-10-06-v0.10.2',
  version: '0.10.2',
  date: 'October 6, 2026',
  title: 'Discuss Your Chat in Agent Studio',
  releaseUrl: 'https://github.com/alliance-genome/agr_ai_curation/releases/tag/v0.10.2',
  sections: [
    {
      heading: 'From a Chat to Your Next Step',
      bullets: [
        'Choose Open in Agent Studio from a chat message to ask how the answer was produced, look into validation, or discuss changes you want to make.',
        'Fixed the trace lookup error that prevented Studio from inspecting the records behind a chat. Studio can use those records to explain what ran and what was checked.',
        'The original chat stays linked when you return to your Studio conversation or change tabs. Starting a new Studio chat clears that link.',
        'To turn your conversation into a flow or improve an existing one, continue on the Flows tab. Studio can help you draft the instructions and review proposed changes before you apply them.',
      ],
    },
  ],
};

export default entry;
