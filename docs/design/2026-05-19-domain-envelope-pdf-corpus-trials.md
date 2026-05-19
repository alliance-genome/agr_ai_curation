# Domain Envelope Real-PDF Corpus Trials

Date: 2026-05-19
Sandbox backend: `http://192.168.86.44:8900`
Sandbox auth mode: dev-mode user (`dev-user-123`) because no `TESTING_API_KEY` was unlocked in this shell.
Runner: `scripts/testing/domain_envelope_pdf_corpus.py`

These trials exercised real uploaded PDFs through the live document upload, PDFX processing, Agent Studio flow execution, domain-envelope extraction, and automatic validator attachment metadata.

## 2026-05-19 Focused Rerun After Evidence Propagation Fixes

After `1501e3bb` and `e8fe5137`, I reran the gene and allele trials against the rebuilt main sandbox at backend port `8900`.

| Domain | Document ID | Flow ID | Flow run ID | Outcome |
| --- | --- | --- | --- | --- |
| Gene | `e3da0e79-2080-4ce4-aa89-68eb7650e9c8` | `36cc32c9-d311-41de-a738-26dc364b5edf` | `ceea9e6d-0f15-46db-88ae-3b10953cbf52` | Passed with `total_evidence_records: 1`, step evidence count `1`, and JSON evidence export count `1`. |
| Allele | `f4a51a32-9b1d-4b32-8188-0633ae45dc78` | `2d123857-6fcd-4d0f-b9f7-3576af5dfea4` | `6855dca2-530c-4e37-9723-c6952d81f20d` | Passed with `total_evidence_records: 1`, step evidence count `1`, and JSON evidence export count `1`. Allele validator lookup events still ended as unresolved `validator_agent_error`, which is separate from evidence propagation. |

The focused rerun wrote the latest raw gene and allele artifacts to:

- `docs/design/pdf-corpus-trials/gene_drosophila_crb_rhabdomere.json`
- `docs/design/pdf-corpus-trials/allele_drosophila_notch_facet_glossy.json`

## 2026-05-19 Remaining Single-Domain Rerun

After the same fixes, I reran the disease, chemical condition, phenotype, and gene-expression trials. These flows completed without SSE error events and `record_evidence` tool calls succeeded internally, but no extraction candidate was persisted for flow evidence export. Each flow finished with `total_evidence_records: 0` and the export endpoint returned `404 {"detail":"Flow run evidence not found"}`.

| Domain | Document ID | Flow ID | Flow run ID | Outcome |
| --- | --- | --- | --- | --- |
| Disease | `bdc27c68-f0f7-4c20-bcaa-bb82a0c06a89` | `7f7d96c5-2e6b-499e-b60d-c8ec939bed97` | `4dbfabba-3c51-469c-9de3-5b1ffed4a139` | Flow completed, but supervisor summarized a persistent extraction-tool failure; evidence export not found. |
| Chemical condition | `3c8b2e96-cedb-4fd7-bb41-d6493b2ec9ed` | `031c04d4-98f5-46e1-8e65-06d60eb26db3` | `0aa39575-30fa-4598-9e1a-1a2c2b3f40bc` | Flow completed, but supervisor summarized a persistent chemical extraction-tool failure; evidence export not found. |
| Phenotype | `1856f74d-09e9-4099-a600-be4b3aa05368` | `2365c1a9-e41e-4b16-98e8-400ff1ac86c7` | `6cecfa92-74f2-4fe7-a00f-e81c18591fdc` | Flow completed, but supervisor summarized a persistent extraction-tool failure; evidence export not found. |
| Gene expression | `4ae426a1-43b0-40f3-8ce8-b3f6120b63c2` | `37aa52e8-dfcc-4297-8335-10fe7007f8d3` | `b0a13e39-39b1-4b31-a10c-996c5eb17a6a` | Flow completed, but supervisor summarized a persistent gene-expression extraction-tool failure; evidence export not found. |

This later rerun wrote the current `docs/design/pdf-corpus-trials/summary.json`.

## Corpus

| Domain | Paper | Source | Organism | Document ID | Flow ID | Outcome |
| --- | --- | --- | --- | --- | --- | --- |
| Gene | Crumbs and the apical spectrin cytoskeleton regulate R8 cell fate in the Drosophila eye | PMID 34097697, PMCID PMC8211197, DOI 10.1371/journal.pgen.1009146 | Drosophila melanogaster | `e3da0e79-2080-4ce4-aa89-68eb7650e9c8` | `36cc32c9-d311-41de-a738-26dc364b5edf` | Focused rerun passed; final answer resolved `crb` to `FB:FBgn0259685`, flow step evidence count was `1`, and evidence export count was `1`. |
| Allele | Notch Controls Cell Adhesion in the Drosophila Eye | PMID 24415930, PMCID PMC3886913, DOI 10.1371/journal.pgen.1004087 | Drosophila melanogaster | `f4a51a32-9b1d-4b32-8188-0633ae45dc78` | `2d123857-6fcd-4d0f-b9f7-3576af5dfea4` | Focused rerun passed evidence gating and export with one persisted evidence record; allele validator lookup still reported unresolved `validator_agent_error`. |
| Disease | Network Analysis of a Pkd1-Mouse Model of Autosomal Dominant Polycystic Kidney Disease Identifies HNF4alpha as a Disease Modifier | PMID 23209420, PMCID PMC3516559, DOI 10.1371/journal.pgen.1003053 | Mus musculus | `bdc27c68-f0f7-4c20-bcaa-bb82a0c06a89` | `7f7d96c5-2e6b-499e-b60d-c8ec939bed97` | Rerun completed, but supervisor reported persistent extraction-tool failure and zero evidence records. |
| Chemical condition | Small molecule screen in embryonic zebrafish using modular variations to target segmentation | PMID 29196643, PMCID PMC5711842, DOI 10.1038/s41467-017-01469-5 | Danio rerio | `3c8b2e96-cedb-4fd7-bb41-d6493b2ec9ed` | `031c04d4-98f5-46e1-8e65-06d60eb26db3` | Rerun completed, but supervisor reported extraction-tool failure and zero evidence records. |
| Phenotype | Joint Molecule Resolution Requires the Redundant Activities of MUS-81 and XPF-1 during Caenorhabditis elegans Meiosis | PMID 23874212, PMCID PMC3715453, DOI 10.1371/journal.pgen.1003582 | Caenorhabditis elegans | `1856f74d-09e9-4099-a600-be4b3aa05368` | `2365c1a9-e41e-4b16-98e8-400ff1ac86c7` | Rerun completed, but supervisor reported extraction-tool failure and zero evidence records. |
| Gene expression | Expression and knockdown of zebrafish folliculin suggests requirement for embryonic brain morphogenesis | PMID 27391801, PMCID PMC4939010, DOI 10.1186/s12861-016-0119-8 | Danio rerio | `4ae426a1-43b0-40f3-8ce8-b3f6120b63c2` | `37aa52e8-dfcc-4297-8335-10fe7007f8d3` | Rerun completed after using Agent Studio ID `gene_expression`; multiple `record_evidence` calls succeeded at tool level, but the supervisor still reported extraction failure and zero exported evidence records. |
| Cross-domain | Small molecule screen in embryonic zebrafish using modular variations to target segmentation | PMID 29196643, PMCID PMC5711842, DOI 10.1038/s41467-017-01469-5 | Danio rerio | `a0bb1a47-1e69-42eb-9cf2-26b9caef2c66` | `2df7be77-de95-43c8-bc7d-92c5de9fc43d` | Stopped at chemical extraction issue; phenotype and gene steps did not produce useful output. |

Raw per-trial JSON evidence is in `docs/design/pdf-corpus-trials/`.

## Findings

1. The PDF upload and PDFX processing path worked for all selected real PDFs once the PDF worker woke up.
2. The first gene corpus run revealed a sandbox cleanup problem: deleting an uploaded document with `domain_envelopes` dependents left a SQL row that later blocked duplicate-content upload with a foreign-key violation. I removed only that orphaned dev-sandbox corpus row set before continuing.
3. Active validator attachment metadata was present in created flow definitions, including `alliance_gene_reference_lookup` for gene, allele validator bindings for allele, chemical validator bindings for chemical condition, phenotype ontology validator metadata, and gene-expression data-provider/relation vocabulary bindings.
4. The original gene trial produced the intended final materialized identity (`FB:FBgn0259685`) in the supervisor answer, but the flow evidence export reported zero persisted evidence records. The focused rerun after the evidence propagation fix exported one evidence record.
5. The original allele trial failed before validator result materialization because supporting envelope objects were treated as missing evidence refs. The focused rerun after role-aware evidence guarding passed evidence gating and exported one evidence record.
6. The remaining non-gene trials mostly failed before validator result materialization because extractor outputs did not preserve evidence record references in the required envelope shape, or the supervisor summarized the specialist tool failure.
7. Gene-expression required the Agent Studio flow agent ID `gene_expression`, not `gene_expression_extraction`, even though package metadata and runtime inventory still expose `gene_expression_extraction`.

## Remaining Work

- Re-run disease, chemical condition, phenotype, gene expression, and cross-domain trials after applying the same evidence-envelope cleanup to any remaining domain-specific extractor shapes.
- Investigate allele `allele_pending_envelope_validator` unresolved `validator_agent_error` lookup attempts; the evidence path now survives, but validator request selection is still empty in the rerun artifacts.
- Capture actual validator request/result payloads and `lookup_attempts` for every domain after extractor outputs survive evidence gating.
- Decide whether the sandbox document delete endpoint should cascade or block cleanly when domain envelopes reference the document.
