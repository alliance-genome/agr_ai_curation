import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-09-07-v0.9.5',
  version: '0.9.5',
  date: 'September 7, 2026',
  title: 'Custom data extraction, a rebuilt AI Chat, and faster flows',
  releaseUrl: 'https://github.com/alliance-genome/agr_ai_curation/releases/tag/v0.9.5',
  sections: [
    {
      heading: 'Extract the data you need',
      text: 'You can now design a custom extraction agent around the information you want from a paper, such as stocks, reagents, or measurements.',
      bullets: [
        'Choose Custom data extraction in Agent Workshop. Name the kind of item to collect, then choose its details and describe what should count as one item.',
        'Keep related details together, such as a supplier name and catalog number. Decide which answers are required and attach available validators to individual details or their parts.',
        'The saved structure keeps the same fields across runs. Custom records remain separate from supported Alliance submission formats.',
      ],
    },
    {
      heading: 'Build agents and flows with AI Chat',
      text: 'AI Chat can work through a flow with you one step at a time, starting with what you want to extract and helping you choose the agents and output.',
      bullets: [
        'Ask it to edit prompts, define custom fields, attach validators, or configure output. Review its proposed changes before applying them.',
        'Chat has clearer progress messages, a stop button, and a way to start a new conversation. Drafts are kept as you move between Workshop and flows.',
      ],
    },
    {
      heading: 'Spend less time waiting',
      bullets: [
        'Validation can process more items at once and avoids repeated setup and unnecessary follow-up work.',
        'Direct structured export writes selected fields to CSV, TSV, or JSON without another AI formatting step. The prompts for that output step are kept but do not run in this mode; extraction and validation still run as configured.',
        'In repeated dev tests, a sample paper-to-export workflow completed about 27% faster. The improvement will vary with the paper and workflow.',
      ],
    },
  ],
};

export default entry;
