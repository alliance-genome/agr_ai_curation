# Domain Envelope Real-PDF Corpus Trials

Date: 2026-05-19
Sandbox backend: `http://192.168.86.44:8900`
Sandbox auth mode: dev-mode user (`dev-user-123`) because no `TESTING_API_KEY` was unlocked in this shell.
Runner: `scripts/testing/domain_envelope_pdf_corpus.py`

These trials exercised real uploaded PDFs through the live document upload, PDFX processing, Agent Studio flow execution, domain-envelope extraction, and automatic validator attachment metadata.

## Corpus

| Domain | Paper | Source | Organism | Document ID | Flow ID | Outcome |
| --- | --- | --- | --- | --- | --- | --- |
| Gene | Crumbs and the apical spectrin cytoskeleton regulate R8 cell fate in the Drosophila eye | PMID 34097697, PMCID PMC8211197, DOI 10.1371/journal.pgen.1009146 | Drosophila melanogaster | `6b2958cb-0cdf-41b0-98f6-51d076375bd9` | `2a524b09-e8cf-449e-ba96-efbd38df26fb` | Completed; final answer resolved `crb` to `FB:FBgn0259685`, but no persisted evidence records were exported. |
| Allele | Notch Controls Cell Adhesion in the Drosophila Eye | PMID 24415930, PMCID PMC3886913, DOI 10.1371/journal.pgen.1004087 | Drosophila melanogaster | `505f6ec6-fe61-4c3d-8c2b-254231948ac2` | `f369b4c2-1ef3-4bde-a07f-ad33a43195c3` | Failed in specialist gating: live evidence existed, but retained envelope objects lacked required evidence record references. |
| Disease | Network Analysis of a Pkd1-Mouse Model of Autosomal Dominant Polycystic Kidney Disease Identifies HNF4alpha as a Disease Modifier | PMID 23209420, PMCID PMC3516559, DOI 10.1371/journal.pgen.1003053 | Mus musculus | `b0e7ce22-0d9f-4f93-be99-4ef901a96968` | `b52f9920-7529-43a4-8e0a-609dbfe44451` | Flow completed, but supervisor reported persistent extraction-tool failure and zero evidence records. |
| Chemical condition | Small molecule screen in embryonic zebrafish using modular variations to target segmentation | PMID 29196643, PMCID PMC5711842, DOI 10.1038/s41467-017-01469-5 | Danio rerio | `a0bb1a47-1e69-42eb-9cf2-26b9caef2c66` | `5de0c792-eb69-4047-a131-00a28ba7ce16` | Flow completed, but supervisor reported extraction-tool failure and zero evidence records. |
| Phenotype | Joint Molecule Resolution Requires the Redundant Activities of MUS-81 and XPF-1 during Caenorhabditis elegans Meiosis | PMID 23874212, PMCID PMC3715453, DOI 10.1371/journal.pgen.1003582 | Caenorhabditis elegans | `1aae256e-dabb-4c87-91cc-96555787364d` | `49e95baf-1c22-4486-8b43-dc4c29932529` | Flow completed, but supervisor reported extraction-tool failure and zero evidence records. |
| Gene expression | Expression and knockdown of zebrafish folliculin suggests requirement for embryonic brain morphogenesis | PMID 27391801, PMCID PMC4939010, DOI 10.1186/s12861-016-0119-8 | Danio rerio | `d0d4f8af-1485-41d0-aa76-37bb415ddbfa` | `92499f22-85fc-44ce-826b-c3fde79facc1` | Flow completed after using Agent Studio ID `gene_expression`; multiple `record_evidence` calls succeeded at tool level, but the supervisor still reported extraction failure and zero exported evidence records. |
| Cross-domain | Small molecule screen in embryonic zebrafish using modular variations to target segmentation | PMID 29196643, PMCID PMC5711842, DOI 10.1038/s41467-017-01469-5 | Danio rerio | `a0bb1a47-1e69-42eb-9cf2-26b9caef2c66` | `2df7be77-de95-43c8-bc7d-92c5de9fc43d` | Stopped at chemical extraction issue; phenotype and gene steps did not produce useful output. |

Raw per-trial JSON evidence is in `docs/design/pdf-corpus-trials/`.

## Findings

1. The PDF upload and PDFX processing path worked for all selected real PDFs once the PDF worker woke up.
2. The first gene corpus run revealed a sandbox cleanup problem: deleting an uploaded document with `domain_envelopes` dependents left a SQL row that later blocked duplicate-content upload with a foreign-key violation. I removed only that orphaned dev-sandbox corpus row set before continuing.
3. Active validator attachment metadata was present in created flow definitions, including `alliance_gene_reference_lookup` for gene, allele validator bindings for allele, chemical validator bindings for chemical condition, phenotype ontology validator metadata, and gene-expression data-provider/relation vocabulary bindings.
4. The gene trial produced the intended final materialized identity (`FB:FBgn0259685`) in the supervisor answer, but the flow evidence export reported zero persisted evidence records.
5. The non-gene trials mostly failed before validator result materialization because extractor outputs did not preserve evidence record references in the required envelope shape, or the supervisor summarized the specialist tool failure.
6. Gene-expression required the Agent Studio flow agent ID `gene_expression`, not `gene_expression_extraction`, even though package metadata and runtime inventory still expose `gene_expression_extraction`.

## Remaining Work

- Fix evidence-record propagation from extraction envelopes into flow evidence export.
- Re-run the corpus after the evidence propagation fix and require nonzero persisted evidence for every trial.
- Capture actual validator request/result payloads and `lookup_attempts` after extractor outputs survive evidence gating.
- Decide whether the sandbox document delete endpoint should cascade or block cleanly when domain envelopes reference the document.
