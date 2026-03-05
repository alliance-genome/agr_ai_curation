import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-03-03-v0.3.1',
  version: '0.3.1',
  date: 'March 3, 2026',
  title: 'AI Curation Platform Update',
  sections: [
    {
      heading: 'New Extraction Agents',
      text: 'Six new specialized extraction agents now pull structured data directly from uploaded PDFs.',
      bullets: [
        'Gene Extractor: harvests gene mentions, resolves multi-species symbol ambiguity, and normalizes to Alliance identifiers.',
        'Allele/Variant Extractor: captures allele notation formats across supported organisms and separates alleles from strains/tools.',
        'Phenotype Extractor: decomposes composite phenotype descriptions into individual assertions with context.',
        'Disease Extractor: identifies disease associations and classifies annotation role and relation type.',
        'Chemical Extractor: extracts experimentally supported chemical entities with role classification.',
        'Gene Expression Extractor improvements: WormBase curation feedback codified as prompt overlays and exclusion rules (KANBAN-1001).',
      ],
    },
    {
      heading: 'MOD-Specific Group Rules',
      text: 'Each extractor now supports per-MOD customization via group rules (WB, FB, MGI, HGNC, SGD, ZFIN, RGD) with MOD-aware nomenclature and disambiguation guidance.',
    },
    {
      heading: 'Chat Persistence Across Navigation',
      text: 'In-progress chat now persists when moving between Home and Agent Studio.',
    },
    {
      heading: 'Under The Hood',
      bullets: [
        'Bulk AGR query support for faster gene/allele normalization.',
        'PDF extraction health checks decoupled from worker sleep/wake state.',
        'Shared exclusion taxonomy and contract fields across extractor agents.',
        'Significant backend test coverage expansion.',
        'Chunking overlap infinite loop fix.',
        'Flow execution and Groq model routing fixes.',
      ],
    },
  ],
};

export default entry;
