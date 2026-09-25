import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  "id": "2026-09-25-v0.9.22",
  "version": "0.9.22",
  "date": "September 25, 2026",
  "title": "Clearer extracted values and stronger validation",
  "releaseUrl": "https://github.com/alliance-genome/agr_ai_curation/releases/tag/v0.9.22",
  "sections": [
    {
      "heading": "Paper wording and validated values",
      "bullets": [
        "Results distinguish the wording extracted from the paper from names and identifiers confirmed by validation. Unresolved values stay visibly unresolved rather than appearing to be confirmed matches.",
        "Extraction agents read the paper; validation agents perform the database searches. Identifiers printed in the paper remain proposals until checked. Fixed mappings, such as standard evidence-code mappings, remain available during extraction.",
        "New extractions include the agent's explanation of why each item was selected, alongside its supporting evidence. Older results may lack this explanation."
      ]
    },
    {
      "heading": "Review and downloadable results",
      "bullets": [
        "Curators can accept or replace an unresolved identity during review. These decisions are recorded as curator overrides, separately from database-confirmed matches.",
        "Downloaded results preserve each selected field's own value, including multiple phenotype terms and structured lists. Unresolved markers identify the affected value instead of obscuring the whole row.",
        "To export every phenotype term, select the full phenotype-term list rather than only the primary display label. Existing saved layouts keep their selected fields.",
        "Disease subject checks use the appropriate gene, allele or genotype/strain validator. GO checks improve gene and With/From identification, reference details, and RGD evidence-code and qualifier handling. Confirmed references include PMID or DOI when available.",
        "Phenotype term validation is available for supported organism mappings; subject and reference resolution remain under development.",
        "Allele extraction can finish with no retained findings under the agent's rules. This does not establish biological absence; an interrupted extraction is still reported as a failure."
      ]
    },
    {
      "heading": "Saved agents, flows and models",
      "bullets": [
        "Affected saved agents and flows are updated while preserving curator instructions, apart from the reviewed instruction changes needed to move the ZFIN allele agent to the current extraction workflow.",
        "Updated active extraction agents no longer carry database identity-search tools. If Workshop reports database lookup tools on an older extractor, review and remove those tools before saving; database searches belong in validation steps.",
        "GPT-6 Sol replaces GPT-5.6 Sol and Terra. Existing Minimal reasoning settings become Low; Off settings become Medium to preserve the reasoning behavior those agents previously used.",
        "PDF section detection, long-result handling and agent diagnostics receive reliability improvements.",
        "Flow Builder can restore a missing Initial Instructions step through an explicit action or a reviewed AI Chat proposal. Other steps stay unchanged; unfinished connections still need attention before saving or running.",
        "ZFIN publication IDs can be entered with or without the ZFIN prefix. If a reference cannot be found, import gives a clearer message so you can check the identifier or upload the PDF."
      ]
    },
    {
      "heading": "Workshop guidance",
      "bullets": [
        "Workshop guidance now explains paper wording, unresolved values, curator overrides, and which checks are available for each extraction type.",
        "Ask Workshop to help choose output fields or place list items in separately named CSV/TSV columns. It prepares changes for review; Apply and Save remain your decisions. JSON downloads keep their lists intact.",
        "Saved-flow checks inspect the selected agent revisions and output settings. Guidance also explains how to inspect long results without rerunning extraction and how to recognize incomplete checks or missing cost information. Larger checks may require a follow-up message to finish; do not treat an incomplete check as a verified flow."
      ]
    },
    {
      "heading": "When returning to earlier results",
      "bullets": [
        "Re-extract older results to use the new validation behavior, especially gene-expression and disease results created in the previous format. Re-validating old results does not supply paper wording that was not saved at extraction time.",
        "Curation-screen submission remains under development. This update does not enable submission to the Alliance; flow CSV, TSV and JSON downloads remain separate from that work."
      ]
    }
  ]
};

export default entry;
