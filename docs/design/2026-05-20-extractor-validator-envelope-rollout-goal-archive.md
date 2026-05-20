# Goal: Roll Out Extractor-to-Validator Envelope Cleanup Across Agents

## Current State

We are mid-cleanup on the new domain-envelope path for chat extraction and validation.

Reference design doc:

- `docs/design/2026-05-19-gene-extractor-validator-identity-boundary.md`

Current HEAD: `8e7ca7e7b4342701f05d9cd12d6ce7fbb446c551` (`origin/main`).

Latest confirmed baseline behavior:

- The gene extractor now emits a domain-envelope extraction.
- Chat runtime dispatches active domain-pack validators before the supervisor summarizes the extractor result.
- The supervisor should receive the validated/materialized envelope, not the raw extractor proposal.
- Package-scoped domain validator output schemas use relaxed OpenAI structured-output conversion.
- Chat domain-envelope validation dispatch emits synthetic validator lookup audit events from `lookup_attempts[]`.
- Dispatch completion is marked unsuccessful when validator lookup attempts report `outcome: "error"`.
- Domain validator result normalization now preserves explicit `blocked` lookup attempts, so a validator can report infrastructure/tooling blockage without being collapsed into a generic agent error.
- Non-dispatch/structural validator bindings are no longer invoked when all non-literal optional selectors are absent.
- LinkML-required model slots are kept strict: disease relation/data provider and gene-expression relation/data provider selectors are required or defaulted from model-backed constants instead of relaxed away.
- FlyBase allele lookup now retries common bracket/superscript/collapsed symbol variants for PDF text such as `Nfa-g`, `N[fa-g]`, and `N<sup>fa-g</sup>`.
- Chemical condition ontology lookup uses exact matching for validator identity checks.
- The gene extraction persistence path filters unsupported uppercase+digit ZFIN chemical-like mentions, such as `SB225002`, when no gene identity hint is present.
- Sync validator dispatch now detects when it is called from an already-running event loop and executes the package-owned validator agent in a worker thread, fixing chat/workspace bootstrap validation.
- Dev-release smoke workspace checks now accept domain-envelope projection-backed workspaces when legacy `entity_tags` are intentionally absent.
- Phenotype ontology label validation now preflight-blocks unsupported provider/taxon contexts before the ontology agent runs. ZFIN/NCBITaxon:7955 phenotype labels no longer fall through to mouse/worm MP/WBPhenotype searches when no DB-verified ZP mapping is active.
- Phenotype extraction persistence now materializes nested `PhenotypeAnnotation.payload.phenotype_terms[]` entries as standalone `PhenotypeTerm` support objects so active ontology validation runs even when the LLM nests the unresolved term under the annotation.
- Validator output identity mismatches are now rejected as controlled `invalid_schema` unresolved results. The dispatcher no longer rewrites stale request, binding, agent, or target identity fields onto otherwise resolved validator output.
- Structural pending-envelope data checks with no selector/result contract are no longer active validator bindings. Allele, disease, and chemical pending data checks are under-development metadata, and a contract test prevents empty active validator bindings from reappearing.
- The Crumbs/crb unit fixture now documents why `FB:FBgn0000368` remains present as an obsolete/internal FlyBase row while expected resolution stays on the real-PDF-backed current identity `FB:FBgn0259685`.
- Gene validator prompt alignment is contract-tested against the gene domain pack: the prompt treats `resolved_objects` as diagnostic only and the pack materializes scalar `resolved_values` onto `gene_mention_evidence`.
- Chemical and disease active validator capability metadata now matches the active validator bindings that runtime actually dispatches. A contract test requires active chemical/disease binding IDs to appear in `validators.active`.
- Planned chemical/disease experimental-condition validators now select `evidence_record.path: verified_quote`, matching the evidence records that extraction actually emits. A registry-level contract test prevents `path: quote` from reappearing in Alliance validator evidence selectors.
- The standalone disease validation specialist now uses the package-owned `agr_curation_query` ontology helpers instead of direct `curation_db_sql`. Active disease domain-pack bindings continue to dispatch through shared `ontology_term_validation`, `controlled_vocabulary_validation`, and `data_provider_validation`, and a contract test prevents active disease bindings from routing back to the direct SQL specialist.
- Agent Studio / Chat with Claude now exposes read-only `get_tool_inventory` and `get_tool_details` diagnostic tools. The tools report global or agent-specific runtime tool metadata, expanded method-level helpers, source files, parameter documentation, and agent-specific multi-method context before Opus answers what a specialist, extractor, or validator can do.
- `get_domain_envelope_state` now returns bounded `validator_summaries` reconstructed from persisted validation finding details. Each summary includes validator binding/agent identity, target, selected inputs, input selectors, expected result fields, result status, resolved values, missing fields, lookup attempts, curator message, failure classification, and expected-result materialization paths.
- Agent Studio Opus prompt architecture and the `get_prompt` diagnostic docs now list current domain-envelope extractors, validator/resolver agents, and lookup specialists explicitly. The prompt surfaces `phenotype_extractor`, `controlled_vocabulary_validation`, `data_provider_validation`, `reference_validation`, `experimental_condition_validation`, and `agm_validation`, and treats `gene`/`allele`/`disease`/`chemical` as legacy validator aliases where applicable.
- Agent Studio `OpusChat` tool-call rendering now formats current `agr_curation_query` method payloads as curator-readable lines, including `method`, `gene_id`, `gene_symbol`, `data_provider`, `ontology_term_type`, `curie`, vocabulary, reference, subject, and taxon fields, instead of falling back to raw JSON for current package-query calls.
- Sandbox document deletion now has an explicit cascade decision for domain-envelope artifacts. Document cleanup removes document-owned domain envelope projection rows, history rows, validation findings, object indexes, and envelope rows before deleting the PDF document, instead of leaving `NO ACTION` FK blockers or ambiguous half-cleanups.
- Gene-expression active validator metadata now matches the runtime active bindings for LinkML-required `relation.name` and `data_provider.abbreviation`. Subject gene, source reference, assay/stage/anatomy/cellular-component/UBERON/GO ontology context, and reagent-context materialization remain explicit under-development gaps, and the extractor prompt no longer implies those fields are already database-validated.
- Agent Studio now treats the gene-expression flow/prompt alias `gene_expression` and the packaged agent ID `gene_expression_extraction` as an explicit equivalent pair for prompt lookup, flow validation, and domain-pack validation-plan inspection. This resolves the PDF-corpus identity mismatch without changing or relaxing LinkML-required gene-expression fields.
- Phenotype active validator metadata now matches the runtime active binding for `phenotype_term_ontology_validator`. The remaining phenotype ontology gap is explicitly limited to additional provider/taxon mappings outside the active WB/MGI policy, while subject and reference validators remain under development.
- Allele active validator metadata now advertises `allele_mention_reference_validation`; pending envelope data checks and source reference validation remain under-development metadata. The shared metadata contract now requires active validator bindings to appear in `validators.active` across allele, chemical, disease, gene expression, and phenotype packs.
- Chat-time active validator dispatch is now unit-covered for every launchable envelope-backed extractor with active validators: allele, disease, chemical, phenotype, gene expression, and the existing gene reference path. The new coverage proves the chat dispatch hook resolves the launchable agent, selects real active domain-pack bindings, runs package-scoped validators before returning the envelope, and emits dispatch counts.
- Live Agent Studio validation-plan inspection in the main sandbox at `24b8c966` confirms the corrected active/under-development metadata is visible through `get_domain_pack_validation_plan`: allele exposes `allele_mention_reference_validation` as active metadata and an active default-enabled binding, while `allele_pending_envelope_validator` and `source_reference_validation` remain under development; phenotype exposes `phenotype_term_ontology_validator` as active metadata and an active default-enabled binding, `phenotype.additional_provider_ontology_mappings` as under-development planning metadata, and no stale `phenotype.ontology_term_resolution` entry.
- The cross-agent boundary design inventory is refreshed through `37e118e9`: allele, chemical condition, and disease no longer list structural pending-envelope/data-check bindings as active dispatch evidence, and the disease row now reflects the shared active ontology/CV/data-provider validators instead of implying the standalone disease specialist is an active binding.
- Gene and allele extractor agent metadata no longer says the extractor performs database-assisted normalization. The deployed metadata describes validator-ready identity hints, and a unit guard now checks all launchable envelope-backed extractors expose only extraction-safe tools and avoid database-normalization wording in agent descriptions.
- Flow execution now carries validator request/result metadata, selected inputs, expected result fields, lookup attempts, and curator messages into completed-step validation metadata, then emits synthetic `domain_validator_lookup` audit events for automatic flow validator groups. The real-PDF corpus helper now has a tightened validator-audit gate that requires expected active binding lookup events and fails `SPECIALIST_TEXT_FALLBACK_SUCCESS` by default.
- The tightened corpus fallback switch is diagnostic-only. `--allow-specialist-text-fallback` lets a debugging run continue while preserving fallback counts; real gate runs leave it off, and `SPECIALIST_TEXT_FALLBACK_SUCCESS` remains a failure.
- Disease extractor schema normalization now fills deterministic pending-envelope scaffold fields from already-present payload/evidence metadata before strict disease validation. This copies payload evidence IDs to the curatable object, mirrors `schema_ref.definition_state`, creates raw-mention metadata from retained disease mentions when missing, and adds fixed pending/blocking metadata plus metadata refs; it does not relax disease relation/data-provider/ontology requirements.
- Chemical, phenotype, gene-expression, and gene extractor schemas now canonicalize deterministic scaffold gaps seen in the tightened corpus before structured-output validation falls into text recovery. Chemical and phenotype fill support-object refs, evidence-quote refs, blocked export/write metadata, and pinned schema refs from already-present payload/evidence metadata; gene expression fills missing pending refs and pinned schema refs; gene drops unsupported ZFIN uppercase/digit compound-like mentions into explicit exclusions before validation. LinkML-required relation/data-provider/term fields remain strict.
- Cross-domain scaffold variants now keep strict validation while normalizing duplicated scaffold inconsistencies: existing chemical/phenotype support `schema_ref` values are pinned to the checked LinkML commit/state, phenotype subjects marked `resolved` without a subject identifier are downgraded to pending subject resolution, phenotype raw mentions can be inferred from retained assertions, and chunk-id typos are repaired only when evidence ID, quote, page, and section already match. Resolved phenotype subjects that have an identifier but lack required resolved fields still fail validation.
- Flow execution now treats configured step tools as required. A multi-step flow cannot finish successfully if the supervisor narrates later work without calling the later step tools. The cross-domain corpus trial now supplies domain-specific custom prompts to chemical, phenotype, and gene extractor steps, and its tightened gate requires all three expected active validator lookup bindings.
- Agent Studio static extractor descriptions and the configured supervisor prompt now preserve the extractor/validator boundary. Extractors are described as producing validator-ready selector/context fields; database/ontology lookup and materialized IDs are described as validator-owned when active.

Bug already fixed in the current repo baseline:

- Earlier main-sandbox runs showed `Active Validator Dispatch` firing, but the inner package-scoped validator agent failed before calling `agr_curation_query`.
- Failure cause: validator result schemas inherit `DomainValidatorResultBase`, which includes flexible provider/query dicts. The package-scoped validator runner used strict OpenAI structured-output schema conversion, so SDK schema compilation failed with `additionalProperties should not be set for object types`.
- Current baseline relaxes the validator result schema and exposes validator lookup attempts in the audit stream.

Remaining immediate validation:

- No tightened-gate PDF corpus blocker remains after `bb135263`. The full seven-trial real-PDF corpus passes in the main sandbox with all expected active validator lookup audit events, no `SPECIALIST_TEXT_FALLBACK_SUCCESS`, and no validator problem events. The latest `8e7ca7e7` follow-up was prompt/static documentation only and does not change the corpus runtime behavior. A future stable fixture could still make the ZFIN phenotype unsupported-mapping shape less LLM-sensitive, but the runtime path and corpus gate are green.

Passed validation during this LinkML-driven continuation:

```bash
docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/lib/domain_packs/test_validator_dispatch.py tests/unit/lib/openai_agents/test_streaming_tools_helpers.py -q"
```

Result: `32 passed, 2 warnings`.

- Focused selector/schema/validator slice after optional-selector and gene-expression changes: `66 passed, 1 deselected, 1 warning`.
- Broader selector/domain-pack slice after optional-selector and gene-expression changes: `209 passed, 2 deselected, 1 warning`.
- Focused schema/validator/tool slice after blocked lookup handling and allele lookup normalization: `45 passed, 1 warning`.
- Broader rollout/tool slice after blocked lookup handling and allele lookup normalization: `273 passed, 2 deselected, 1 warning`.
- Chemical/domain validation slice after exact-match chemical condition lookup: `82 passed, 1 deselected, 1 warning`.
- Allele/gene/domain validation slice after FlyBase bracket/collapsed symbol normalization and ZFIN drug-like gene guard: `137 passed, 1 deselected, 1 warning`.
- Disease/domain validation slice after requiring disease data-provider selectors: `100 passed, 1 deselected, 1 warning`.
- Focused sanitizer/gene/domain tests after dropping unsupported ZFIN chemical-like gene candidates from persisted results: `93 passed, 1 warning`.
- Event-loop-safe validator dispatch and deterministic workspace pipeline slice after chat/workspace failure: `29 passed, 1 warning`.
- Local dev-release smoke script tests against the actual host script: `31 passed, 2 pytest cache warnings`.
- Phenotype preflight flow/dispatch slice after blocking unsupported provider/taxon contexts: `118 passed, 1 deselected, 1 warning`.
- Nested phenotype-term materialization slice after preserving annotation-nested unresolved terms for validation: `74 passed, 1 warning`.
- Validator identity trust-boundary slice after rejecting stale validator output identities: `50 passed, 1 warning`.
- Alliance domain-pack contract suite after demoting structural pending-envelope validators: `100 passed, 1 warning`.
- AGR curation query path slice after documenting the retired Crumbs fixture row: `16 passed, 1 warning`.
- Gene domain-pack contract slice after locking prompt/materialization alignment: `12 passed, 1 warning`.
- Alliance domain-pack contract suite after reconciling chemical/disease active validator metadata: `102 passed, 1 warning`.
- Alliance domain-pack contract suite after changing planned condition-validator evidence selectors to `verified_quote`: `103 passed, 1 warning`; targeted selector tests: `3 passed, 1 warning`; no remaining `path: quote` matches in Alliance domain packs or their contract tests.
- Disease specialist package-tool boundary slice after removing direct SQL from disease validation: `25 passed, 1 deselected, 1 warning` with the known LinkML cache-helper case deselected.
- Alliance domain-pack/config suite after disease specialist package-tool boundary change: `96 passed, 4 skipped, 13 deselected, 1 warning` with `-m 'not alliance_linkml'`.
- Agent Studio registry/catalog and disease/chemical config slice after disease specialist documentation update: `38 passed, 4 warnings`.
- Agent Studio tool inventory diagnostics and domain-envelope prompt-policy slice: `21 passed, 4 warnings`.
- Domain-envelope state validator-summary and Opus prompt-policy slice: `21 passed, 4 warnings`.
- Agent Studio prompt-target docs/tool-registry slice after updating current extractor/validator prompt targets: `17 passed, 4 warnings`; syntax compile passed with `PYTHONPYCACHEPREFIX=/tmp/agr-ai-curation-pycache python3 -m py_compile backend/src/lib/agent_studio/diagnostic_tools/tool_definitions.py backend/tests/unit/api/test_agent_studio_domain_envelope_prompt_policy.py backend/tests/unit/lib/agent_studio/test_hybrid_tool_registry.py`.
- Frontend OpusChat lookup-rendering slice: `cd frontend && npm run test -- OpusChat.test.tsx --run` -> `11 passed`; scoped TypeScript guard `cd frontend && npm run type-check:changed -- --base origin/main` -> `FRONTEND_TYPECHECK_STATUS=baseline_only`, 65 existing baseline errors outside changed files.
- Current main-sandbox gene chat/workspace smoke at `f40c18d1`: `python3 scripts/testing/dev_release_smoke.py --base-url http://192.168.86.44:8900 --sample-pdf /tmp/agr_domain_envelope_pdf_corpus/gene_drosophila_crb_rhabdomere.pdf --allow-dev-mode-fallback --allow-duplicate-reuse --skip-user-info --skip-flow --skip-batch --chat-model gpt-4o --chat-message 'Briefly say whether the loaded paper discusses Crumbs/crb; mention crb if present.' --chat-timeout-seconds 300 --processing-timeout-seconds 900 --evidence-dir /tmp/agr_ai_curation_chat_smoke_f40c18d1` -> `PASS (partial/debug run; omitted or relaxed: user_info, flow, batch, rerank_provider_smoke, dev_mode_fallback, duplicate_reuse)`.
- Document cleanup cascade slice after deciding to remove document-owned domain-envelope artifacts on PDF delete: syntax compile passed with `PYTHONPYCACHEPREFIX=/tmp/agr-ai-curation-pycache python3 -m py_compile backend/src/lib/document_cleanup.py backend/tests/unit/lib/test_document_cleanup.py`; `docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/lib/test_document_cleanup.py tests/unit/api/test_documents_runtime_endpoints.py tests/unit/lib/test_weaviate_documents_runtime.py -q"` -> `52 passed, 1 warning`; `git diff --check` passed.
- Gene-expression validator metadata/prompt gap slice: syntax compile passed for `backend/tests/contract/alliance/domain_packs/test_gene_expression_domain_pack.py`; `docker compose -f docker-compose.test.yml run --rm backend-contract-tests bash -lc "python -m pytest tests/contract/alliance/domain_packs/test_gene_expression_domain_pack.py -q -m 'not alliance_linkml'"` -> `7 passed, 2 deselected, 1 warning`; `docker compose -f docker-compose.test.yml run --rm backend-contract-tests bash -lc "python -m pytest tests/contract/alliance/domain_packs/test_validation_metadata.py -q"` -> `13 passed, 1 warning`; `docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/test_gene_expression_prompt_policy.py -q"` -> `11 passed, 1 warning`; `git diff --check` passed.
- Gene-expression Agent Studio alias/prompt-plan slice: syntax compile passed for the changed Agent Studio tests/tool docs; `git diff --check` passed; `docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/lib/agent_studio/test_catalog_service_prompt_keys.py tests/unit/lib/agent_studio/test_registry_builder.py tests/unit/lib/agent_studio/test_domain_envelope_tools.py tests/unit/lib/agent_studio/test_flow_tools.py tests/unit/api/test_agent_studio_domain_envelope_prompt_policy.py tests/unit/api/test_agent_studio_domain_envelope_tools.py tests/unit/lib/agent_studio/test_hybrid_tool_registry.py -q"` -> `78 passed, 4 warnings`.
- Phenotype validator metadata alignment slice: syntax compile passed for `backend/tests/contract/alliance/domain_packs/test_phenotype_domain_pack.py` and `backend/tests/contract/alliance/domain_packs/test_validation_metadata.py`; `git diff --check` passed; `docker compose -f docker-compose.test.yml run --rm backend-contract-tests bash -lc "python -m pytest tests/contract/alliance/domain_packs/test_phenotype_domain_pack.py tests/contract/alliance/domain_packs/test_validation_metadata.py -q -m 'not alliance_linkml'"` -> `26 passed, 3 deselected, 1 warning`.
- Allele validator metadata alignment slice: syntax compile passed for `backend/tests/contract/alliance/domain_packs/test_allele_domain_pack.py` and `backend/tests/contract/alliance/domain_packs/test_validation_metadata.py`; `git diff --check` passed; `docker compose -f docker-compose.test.yml run --rm backend-contract-tests bash -lc "python -m pytest tests/contract/alliance/domain_packs/test_allele_domain_pack.py tests/contract/alliance/domain_packs/test_validation_metadata.py -q -m 'not alliance_linkml'"` -> `24 passed, 1 deselected, 1 warning`.
- Chat dispatch coverage slice across launchable active-validator domains: syntax compile passed for `backend/tests/unit/lib/openai_agents/test_streaming_tools_helpers.py`; `git diff --check` passed; `docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/lib/openai_agents/test_streaming_tools_helpers.py -q"` -> `29 passed, 1 warning`.
- Cross-agent design-inventory refresh: `git diff --check -- docs/design/2026-05-19-gene-extractor-validator-identity-boundary.md goal.md` passed; commit hook secret scanning passed with gitleaks and TruffleHog. No runtime tests were needed for the docs-only change.
- Extractor metadata/tool-boundary guard: syntax compile passed for `backend/tests/unit/test_domain_envelope_repair_prompt_contract.py`; `git diff --check` passed; `docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/test_domain_envelope_repair_prompt_contract.py -q"` -> `7 passed, 1 warning`.
- Flow validator audit/corpus-gate slice: syntax compile passed for `backend/src/lib/flows/executor.py`, `backend/tests/unit/lib/flows/test_executor.py`, `scripts/testing/domain_envelope_pdf_corpus.py`, and `backend/tests/unit/scripts/test_domain_envelope_pdf_corpus.py`; `git diff --check` passed; `docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/lib/flows/test_executor.py -q"` -> `86 passed, 1 warning`; `docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/api/test_chat_execute_flow_endpoint.py -q"` -> `27 passed, 1 warning`; local checkout `python3 -m pytest backend/tests/unit/scripts/test_domain_envelope_pdf_corpus.py -q` -> `4 passed, 1 pytest cache warning`; stale backend-unit-test image `python -m pytest tests/unit/scripts/test_domain_envelope_pdf_corpus.py -q` -> `4 skipped, 1 warning` because `/app/scripts/testing/domain_envelope_pdf_corpus.py` is not present in that image.
- Corpus diagnostic-preservation follow-up: local checkout `python3 -m pytest backend/tests/unit/scripts/test_domain_envelope_pdf_corpus.py -q` -> `5 passed, 1 pytest cache warning`; syntax compile and `git diff --check` passed for `scripts/testing/domain_envelope_pdf_corpus.py` and `backend/tests/unit/scripts/test_domain_envelope_pdf_corpus.py`.
- Disease scaffold canonicalization slice: syntax compile passed for `packages/alliance/agents/disease_extractor/schema.py` and `backend/tests/unit/test_disease_extractor_domain_envelope_contract.py`; `git diff --check` passed; `docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/test_disease_extractor_domain_envelope_contract.py -q"` -> `30 passed, 1 warning`; `docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/lib/openai_agents/test_streaming_tools_retry_paths.py tests/unit/lib/openai_agents/test_streaming_tools_helpers.py -q"` -> `34 passed, 1 warning`; after extending canonicalization for missing raw mentions and schema-ref definition state, `docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/test_disease_extractor_domain_envelope_contract.py tests/unit/lib/openai_agents/test_streaming_tools_retry_paths.py tests/unit/lib/openai_agents/test_streaming_tools_helpers.py -q"` -> `64 passed, 1 warning`.
- Cross-domain scaffold canonicalization slice: syntax compile passed for chemical, phenotype, gene-expression, and gene extractor schemas plus their focused tests; `git diff --check` passed; `docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/test_chemical_extractor_domain_envelope_contract.py tests/unit/test_phenotype_extractor_domain_envelope_contract.py tests/unit/test_gene_expression_prompt_policy.py tests/unit/test_gene_extractor_domain_envelope_contract.py tests/unit/lib/openai_agents/test_streaming_tools_retry_paths.py tests/unit/lib/openai_agents/test_streaming_tools_helpers.py -q"` -> `112 passed, 1 warning`.
- Cross-domain scaffold-variant follow-up: syntax compile passed for chemical, phenotype, and gene extractor schemas/tests; `git diff --check` passed; `docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/test_chemical_extractor_domain_envelope_contract.py tests/unit/test_phenotype_extractor_domain_envelope_contract.py tests/unit/test_gene_extractor_domain_envelope_contract.py tests/unit/test_gene_expression_prompt_policy.py tests/unit/lib/openai_agents/test_streaming_tools_retry_paths.py tests/unit/lib/openai_agents/test_streaming_tools_helpers.py -q"` -> `113 passed, 1 warning`.
- Complete-flow corpus follow-up: syntax compile passed for `backend/src/lib/flows/executor.py`, `scripts/testing/domain_envelope_pdf_corpus.py`, `backend/tests/unit/lib/flows/test_executor.py`, and `backend/tests/unit/scripts/test_domain_envelope_pdf_corpus.py`; local checkout `python3 -m pytest backend/tests/unit/scripts/test_domain_envelope_pdf_corpus.py -q` -> `6 passed, 1 pytest cache warning`; `docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/lib/flows/test_executor.py -q"` -> `87 passed, 1 warning`; `docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/api/test_chat_execute_flow_endpoint.py -q"` -> `27 passed, 1 warning`; `git diff --check` and `git diff --cached --check` passed.
- Final prompt/static documentation boundary cleanup: syntax compile passed for `backend/src/lib/agent_studio/registry_builder.py`, `backend/tests/unit/lib/agent_studio/test_registry_builder.py`, `backend/tests/unit/test_supervisor_prompt_policy.py`, and `backend/tests/unit/api/test_agent_studio_domain_envelope_prompt_policy.py`; `docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/lib/agent_studio/test_registry_builder.py tests/unit/test_supervisor_prompt_policy.py tests/unit/api/test_agent_studio_domain_envelope_prompt_policy.py tests/unit/test_domain_envelope_repair_prompt_contract.py -q"` -> `29 passed, 1 warning`; `git diff --check` passed; stale extractor-owned lookup wording search across the static Agent Studio docs, supervisor prompt, Alliance package prompts, and Agent Studio prompt knowledge now only matches regression-test forbidden-string assertions, not source prompt/doc copy.
- Focused cross-domain corpus after deployed `bb135263`: `python3 scripts/testing/domain_envelope_pdf_corpus.py --base-url http://192.168.86.44:8900 --allow-dev-mode-fallback --allow-duplicate-reuse --trial cross_domain --flow-timeout-seconds 900 --processing-timeout-seconds 1200` -> `pass`, flow run `cf091ea8-c628-450c-b91f-4bd84c7e23d0`, observed all three expected bindings, zero specialist text fallbacks, zero validator problem events.
- Full real-PDF corpus after deployed `bb135263`: `python3 scripts/testing/domain_envelope_pdf_corpus.py --base-url http://192.168.86.44:8900 --allow-dev-mode-fallback --allow-duplicate-reuse --flow-timeout-seconds 900 --processing-timeout-seconds 1200` -> `overall_status=pass`, seven of seven trials passed the tightened gate, zero specialist text fallbacks, zero validator problem events.

Known test-image limitation:

- LinkML class/slot tests that shell out to `/app/scripts/testing/cache_agr_curation_schema.sh` fail in `backend-unit-tests` because that helper is absent from the image. Excluding those helper-dependent tests, the targeted slices above pass.

Known unrelated baseline failure encountered:

- `test_bundled_alliance_removes_retired_ontology_mapping_route` failed because `/app/alliance_agents/ontology_mapping` exists in the test image. The same focused run passed when that baseline case was deselected: `78 passed, 1 deselected, 1 warning`.

## Review Findings From 5.5 High Agents

The review agents agreed that gene is the best-covered reference slice, but the project is not done.

High-priority implementation risks:

- Done: resolved validator outputs without successful lookup evidence are rejected as controlled `invalid_schema` unresolved results before materialization.
- Done: validator identity mismatches now become unresolved `invalid_schema` findings instead of trusted materialized values.
- Done: the gene validator prompt and domain pack are aligned and contract-tested. `resolved_objects` are diagnostic only; scalar `resolved_values` materialize onto `gene_mention_evidence`, and the domain pack declares no separate `Gene` object.
- Done: the unit fixture for `crumbs/crb` intentionally keeps `FB:FBgn0000368` as an obsolete/internal row to test filtering, and comments now document that the expected current identity is `FB:FBgn0259685`.
- Done: active “pending envelope/data check” bindings with empty `input_fields` and empty `expected_result_fields` have been demoted to under-development structural metadata, and `test_alliance_active_validator_bindings_have_dispatch_contracts` prevents empty active package-validator bindings.
- Done: disease validation no longer attaches or prompts for `curation_db_sql`; the standalone disease specialist uses `agr_curation_query`, while active disease domain-pack bindings use shared package validators instead of the disease SQL specialist.
- Done: chemical and disease `validators.active`/`under_development` buckets now agree with `validator_bindings.active` for runtime-dispatched bindings, with a focused contract test covering both packs.
- Done: planned disease/chemical experimental-condition selectors now use `evidence_record.path: verified_quote`, and a contract test guards all Alliance evidence-record selectors against stale `quote` paths.

Documentation and product gaps:

- `docs/design/2026-05-19-gene-extractor-validator-identity-boundary.md` already has the cross-agent inventory. Remaining work is to close the named gaps, not create the inventory from scratch.
- Gene expression already has active `relation_vocabulary_validation` and `data_provider_validation`; its gap is missing anatomy/stage/assay/reagent/gene/reference validation coverage.
- Phenotype currently has active phenotype-term validation, while subject/reference validators remain under development.
- Done: Chat with Claude now has first-class read-only `get_tool_inventory` and `get_tool_details` tools for global and agent-specific runtime tool metadata.
- Done: `get_domain_envelope_state` now exposes bounded validator request/result summaries with selected inputs, expected fields, binding/agent identity, lookup attempts, resolved values, missing fields, and materialization paths.
- Done: Agent Studio Opus prompt architecture and `get_prompt` tool docs list current envelope extractors, validator/resolver agents, and lookup specialists instead of only legacy extraction/DB categories.

## Primary Objective

Apply the same extractor/proposed-field and validator/materialized-field architecture to every envelope-backed curation data type, starting from gene and then extending across the remaining extractor/validator agents.

The key boundary:

- Extractors read the paper and propose paper-grounded candidates.
- Validators use database/API/ontology tools to resolve or reject those proposals.
- The runtime connects extractor output to validators before the supervisor summarizes the result.
- Curators and developers must be able to see what validator ran, what payload it received, what lookup it performed, and why it resolved or failed.

## Tool Boundary Policy

Default extractor tools:

- Document search/read tools.
- Evidence recording tools.
- Species/taxon context lookup is allowed when needed because the extractor has the paper context and is often best positioned to infer organism context.

Default extractor restrictions:

- No AGR curation entity lookup for validating gene/allele/disease/chemical/ontology IDs.
- No gene-name, allele-name, ontology-term, disease-term, or chemical-term database searches intended to resolve identity.
- Extractor fields that came from the paper or paper-context inference should be named `proposed_*`, `*_hint`, or otherwise clearly non-authoritative.

Default validator tools:

- AGR curation query tools.
- Ontology/search tools needed to validate identifiers, labels, term IDs, provider IDs, and taxon/entity consistency.
- No paper-reading requirement unless a specific validator design says it needs paper quotes. The normal validator input should be the `DomainValidationRequest` built from envelope payload fields and evidence records.

Tool implementation policy:

- Prefer existing Python package/runtime tools before creating new tools.
- Inventory tools already declared in package `agent.yaml` files, package tool modules, and `agr_ai_curation_runtime` public helpers before adding anything.
- If an existing package tool already performs the needed lookup, update validator prompts/metadata to use it instead of building a duplicate direct DB helper.
- Add a new tool only when no existing package/runtime tool can express the validator lookup cleanly, or when the existing tool would require unsafe broadening.
- The curation DB tunnel is for focused reconnaissance, schema inspection, and validating what data a tool should hit. It should not become the default runtime path if an API-backed or package-backed tool already exists.

## Live Data Reconnaissance Notes

Use the read-only curation DB tunnel when live Alliance data availability affects a validator design, tool boundary, or bug diagnosis.

Primary helper from a Symphony workspace:

```bash
bash scripts/utilities/symphony_curation_db_psql.sh -- \
  -c "select current_database(), current_user;"
```

If the workspace branch predates the helper, use the source-root helper:

```bash
bash "${SYMPHONY_LOCAL_SOURCE_ROOT}/scripts/utilities/symphony_curation_db_psql.sh" \
  --workspace-dir "$PWD" -- \
  -c "select current_database(), current_user;"
```

Status check:

```bash
bash scripts/utilities/symphony_curation_db_psql.sh --status
```

Rules for DB inspection:

- Use `SELECT` only.
- Prefer `information_schema` discovery first when table shape is unclear.
- Always use focused filters and `LIMIT`.
- Never print, paste, commit, or summarize the contents of `scripts/local_db_tunnel_env.sh`; it contains credentials.
- Treat DB output as design/debug evidence, not as a reason to hard-code table names into provider-neutral core modules.
- For live opt-in tests, prefer the existing `ALLIANCE_LIVE_DB_CONTRACT_TESTS=1` pattern and tunnel-provided `CURATION_DB_URL` or `PERSISTENT_STORE_DB_*` environment.

High-value curation DB tables already noted for identifier/search grounding:

- `biologicalentity`
- `genomicentity`
- `crossreference`
- `genomicentity_crossreference`
- `slotannotation`
- `synonym`
- `ontologyterm`

Literature/reference data:

- Do not open or rely on a direct literature DB tunnel by default.
- Prefer the API or API-backed package tool path for reference/literature lookup.
- Existing reference validator tests and prompts already point at `agr_literature_reference_lookup` with methods such as `get_literature_reference` and `search_literature_references`; inspect and reuse that before designing new reference lookup access.
- If reference/literature live data must be inspected, document why the existing API/package tool path is insufficient before asking for any new tunnel access.

### 2026-05-19 LinkML requiredness check

Pinned schema checkout: `/tmp/agr_curation_schema_cache/agr_curation_schema/1b11d0888f19eba4ca72022200bb7d96b30d4a52`, commit `1b11d0888f19eba4ca72022200bb7d96b30d4a52`. The checkout has the `secure-repo` pre-commit hook installed.

Important schema findings:

- `Allele` inherits required `BiologicalEntity.taxon`. `AlleleMention.taxon.curie` is optional selector context, not a LinkML object field; missing mention taxon must not relax final validator materialization of `allele.taxon`.
- `DiseaseAnnotation` requires `disease_annotation_object`, `disease_annotation_subject`, `relation`, `single_reference`, `evidence_codes`, `data_provider`, and `internal`; DTO ingest requires `disease_relation_name`, `do_term_curie`, `reference_curie`, `data_provider_dto`, and evidence-code fields. Do not make disease relation or data-provider requirements optional to quiet selector findings.
- `condition_relations` is optional on annotations, but a present `ConditionRelation` requires `condition_relation_type`; `ConditionRelationDTO` requires both `condition_relation_type_name` and `condition_dtos`. Dispatch should not treat an absent optional condition-relation array as a missing required model field.
- `GeneExpressionAnnotation` requires `relation`, `data_provider`, `expression_annotation_subject`, `single_reference`, `expression_pattern`, `when_expressed_stage_name`, `where_expressed_statement`, and `internal`. Live curation DB rows have `geneexpressionannotation.relation_id` populated for every checked row, and all checked rows use vocabulary term `is_expressed_in`.

Live curation DB confirmation through the read-only Symphony tunnel:

- `diseaseannotation`: 81,227 rows; all count-checked rows have `diseaseannotationobject_id`, `relation_id`, and `dataprovider_id`.
- `geneexpressionannotation`: 1,911,104 rows; all count-checked rows have `relation_id` and `dataprovider_id`; relation term is `is_expressed_in`.
- `conditionrelation`: 19,549 rows; all count-checked rows have `conditionrelationtype_id`; most are `has_condition`.
- `biologicalentity`: 28,730,790 rows; all count-checked rows have `taxon_id` and `dataprovider_id`.

Implementation consequence: do not broadly relax validator selector inputs. Use optional selector behavior only for genuinely supplemental context or fields conditional on an optional parent object. For LinkML-required slots, preserve required domain-pack fields and fix extractor/converter/defaulting paths instead.

### 2026-05-19 implementation and sandbox evidence

Committed and pushed fixes now on `origin/main`:

- `113c764f` Harden domain validator output normalization.
- `a55c3993` Skip non-dispatch flow validator bindings.
- `eec3a83d` Align validation selectors with LinkML requirements.
- `ca252c45` Tighten gene expression validation selectors.
- `512e34d6` Handle blocked allele lookup attempts.
- `f865d644` Use exact matching for chemical condition class validation.
- `12b798e5` Tighten gene and allele corpus validation.
- `9deca264` Require disease data provider selectors.
- `87430568` Filter non-gene ZFIN compound evidence.
- `265a7610` Run sync validators off loop when needed.
- `9c4008d5` Accept projected workspace smoke payloads.
- `2841ef41` Block unsupported phenotype ontology mappings.
- `4aa30314` Materialize nested phenotype term objects.
- `3763e452` Reject stale validator result identities.
- `36602333` Demote structural envelope validators.
- `62ca5de4` Document retired Crumbs fixture row.
- `0cf952df` Guard gene validator scalar materialization prompt.
- `308ede1b` Align active validator capability metadata.
- `7b153efe` Use verified evidence quotes in planned condition validators.
- `361e9309` Use package lookup for disease validation.
- `15a216d0` Expose Agent Studio tool inventory diagnostics.
- `51fe258b` Summarize validator details in envelope state.
- `a43f0450` Document current Agent Studio prompt targets.
- `f40c18d1` Render AGR curation tool calls readably.
- `f1c58557` Update validator inventory for disease package lookup.
- `8aa4bc19` Cascade document cleanup through domain envelopes.
- `de761e6b` Expose gene expression validation gaps explicitly.
- `5ccfb00b` Document gene expression agent ID aliases.
- `ffca8f29` Align phenotype validator metadata with active binding.
- `f67eaddf` Expose allele validator capability metadata.
- `24b8c966` Cover chat validation dispatch for launchable domains.
- `37e118e9` Refresh validator boundary inventory.
- `558f671f` Guard extractor metadata tool boundaries.
- `2878384e` Surface flow validator audit events.
- `819b2b2f` Preserve tightened corpus gate diagnostics.
- `4d5c2bc6` Canonicalize disease extractor scaffold fields.
- `25687612` Infer disease extractor raw mention scaffold.
- `8d961208` Canonicalize extractor scaffold gaps.
- `c04b9789` Handle cross-domain extractor scaffold variants.
- `bb135263` Enforce complete flow corpus steps.
- `8e7ca7e7` Align extractor boundary docs.

Main sandbox state:

- VM source checkout and VM sandbox checkout `/home/ctabone/.symphony/sandboxes/agr_ai_curation/main` are at `8e7ca7e7b4342701f05d9cd12d6ce7fbb446c551`. The actual `agrmainsandbox-backend-1` container was restarted directly after `8e7ca7e7`; Docker health is `healthy` with container start `2026-05-19T21:39:33.447390174Z`, `/health` returned healthy at `2026-05-19T21:40:17.495970+00:00`, and backend log grep since restart found no `Traceback`, `ERROR`, `validator_agent_error`, `SPECIALIST_TEXT_FALLBACK_SUCCESS`, `AgentRunner.run_sync`, or `incomplete_flow_steps` matches.
- Previous corpus-runtime deployment: VM source checkout and VM sandbox checkout `/home/ctabone/.symphony/sandboxes/agr_ai_curation/main` were at `bb1352631019ab1135529b280b73e28f8cf52adb`. Backend was restarted after `bb135263`; the first host health probes briefly got the expected startup `Empty reply from server`, and `/health` returned healthy at `2026-05-19T21:17:01.106809+00:00`.
- Focused cross-domain corpus rerun after deployed `bb135263`: strict gate `pass`, flow run `cf091ea8-c628-450c-b91f-4bd84c7e23d0`, evidence records `3`, steps `chemical_extractor`, `phenotype_extractor`, and `gene_extractor` each emitted one evidence record, observed all expected bindings (`chemical_condition.chebi_api_lookup`, `phenotype_term_ontology_validator`, `alliance_gene_reference_lookup`), no specialist text fallback events, and no validator problem events.
- Full real-PDF corpus run after deployed `bb135263`: strict gate `pass`, summary artifact `docs/design/pdf-corpus-trials/summary.json`, summary timestamp `2026-05-19T21:19:59.332670+00:00`, overall status `pass`. All seven trials passed with expected active validator lookup audit events, no `SPECIALIST_TEXT_FALLBACK_SUCCESS`, and no validator problem events. Flow runs: gene `78559033-da07-45bc-98c6-622829a696a2`; allele `33b36204-a254-4e1c-94d0-d8f88f073194`; disease `0b564b97-4216-40b3-810b-cf30a4d78e0d`; chemical `56f2b272-9fa5-43af-971f-c22d24bed700`; phenotype `d496934a-99f1-4a83-bf83-eb5aacd74101`; gene expression `aa9cd576-1278-4e97-9e83-6a1a0666c62e`; cross-domain `c037bdca-c29e-4424-8db4-5c138aeec02e`.
- Backend log grep since `2026-05-19T21:17:00Z` found no `AgentRunner.run_sync`, `validator_agent_error`, `Package-scoped validator agent failed`, or `SPECIALIST_TEXT_FALLBACK_SUCCESS` matches. `/health` returned healthy at `2026-05-19T21:31:27.627520+00:00`.
- Focused gene real-PDF corpus trial after deployed `2878384e`: strict gate `pass`, summary timestamp `2026-05-19T20:15:04.482929+00:00`, flow run `f5044d7d-c262-4d13-8428-0a52764674eb`, evidence records `1`, observed `alliance_gene_reference_lookup` lookup audit count `4`, no specialist text fallback events, and no validator problem events.
- Focused disease real-PDF corpus trial after deployed `2878384e`: strict gate `fail`, summary timestamp `2026-05-19T20:16:19.688371+00:00`, flow run `e4943726-7c8e-4bbf-9612-1eebc1448c70`, evidence records `1`. The tightened gate observed active disease validator lookup counts in the trial checks (`disease_ontology_term_lookup: 4`, `disease_relation_cv_lookup: 2`, `disease_data_provider_lookup: 2`), but failed because `SPECIALIST_TEXT_FALLBACK_SUCCESS` occurred once. After deployed `4d5c2bc6`, rerun `dcb6e3c7-c5bf-4eec-bfb5-dc8c932f7997` still failed because the LLM also omitted `metadata.raw_mentions[]` and `schema_ref.definition_state`; those were handled as deterministic scaffold canonicalization, not LinkML requirement relaxation. The same rerun exposed one validator problem event from a chat-dispatch `disease_data_provider_lookup` attempt, while the flow-validation-group data-provider lookup resolved successfully.
- Focused disease real-PDF corpus trial after deployed `25687612`: strict gate `pass`, summary timestamp `2026-05-19T20:29:04.312731+00:00`, flow run `b5ae83bb-2e9e-441e-8986-5ac8bc2176c3`, evidence records `1`, observed lookup counts `disease_ontology_term_lookup: 4`, `disease_relation_cv_lookup: 2`, `disease_data_provider_lookup: 2`, no specialist text fallback events, and no validator problem events.
- Full real-PDF corpus run after deployed `25687612`: strict gate `fail`, summary timestamp `2026-05-19T20:30:54.885059+00:00`. Gene, allele, and disease passed the tightened gate with expected active validator lookup audit events and no specialist text fallback. Chemical (`04905794-9b14-4dc4-a957-59db20a18f37`), phenotype (`63464e0f-7d75-4437-adac-622b24148339`), gene expression (`32cbd0ef-70b8-4a5e-b17c-7a15f20d500a`), and cross-domain (`05c5d84b-12d6-4aa6-a0f2-f29a74d529d0`) all had expected validator lookup audit events and no validator problem events, but failed the tightened gate because `SPECIALIST_TEXT_FALLBACK_SUCCESS` occurred. The remaining blocker is extractor structured-output/schema recovery for those domains, not missing validator dispatch visibility or LinkML-requiredness relaxation.
- `agrmainsandbox` backend, frontend, Postgres, Weaviate, Redis, Langfuse, ClickHouse, and MinIO containers were healthy for the corpus run.
- Sandbox corpus base URL: `http://192.168.86.44:8900`.
- Backend was restarted after `a43f0450` and `/health` returned healthy at `2026-05-19T18:44:50.598484+00:00`.
- Frontend was rebuilt/recreated after `f40c18d1` through `scripts/utilities/symphony_main_sandbox.sh repair`; `/health` returned healthy at `2026-05-19T18:54:54.293494+00:00`, and the frontend returned `HTTP/1.1 200 OK` with `Last-Modified: Tue, 19 May 2026 18:49:54 GMT`.
- Docs-only commit `f1c58557` was pulled into the VM source checkout and main sandbox checkout; backend `/health` remained healthy at `2026-05-19T19:03:14.225237+00:00`.
- Backend cleanup commit `8aa4bc19` was pulled into the VM source checkout and main sandbox checkout; the existing backend container was restarted, and `/health` returned healthy at `2026-05-19T19:11:33.291971+00:00`.
- Gene-expression metadata/prompt commit `de761e6b` was pulled into the VM source checkout and main sandbox checkout; the existing backend container was restarted, and `/health` returned healthy at `2026-05-19T19:17:53.388479+00:00`.
- Gene-expression Agent Studio alias commit `5ccfb00b` was pulled into the VM source checkout and main sandbox checkout; the existing backend container was restarted, and `/health` returned healthy at `2026-05-19T19:25:36.110770+00:00`. The frontend still returned `HTTP/1.1 200 OK`.
- Phenotype metadata commit `ffca8f29` was pulled into the VM source checkout and main sandbox checkout; the existing backend container was restarted, and `/health` returned healthy at `2026-05-19T19:30:04.741335+00:00`. The frontend still returned `HTTP/1.1 200 OK`.
- Allele metadata commit `f67eaddf` was pulled into the VM source checkout and main sandbox checkout; the existing backend container was restarted, and `/health` returned healthy at `2026-05-19T19:32:23.648890+00:00`. The frontend still returned `HTTP/1.1 200 OK`.
- Chat dispatch coverage commit `24b8c966` was pulled into the VM source checkout and main sandbox checkout; the existing backend container was restarted, and `/health` returned healthy at `2026-05-19T19:37:10.726316+00:00`. The frontend still returned `HTTP/1.1 200 OK`.
- Design inventory commit `37e118e9` was pulled into the VM source checkout and main sandbox checkout; no restart was needed for the docs-only change. Backend `/health` returned healthy at `2026-05-19T19:43:44.282948+00:00`, and the frontend returned `HTTP/1.1 200 OK`.
- Extractor metadata commit `558f671f` was pulled into the VM source checkout and main sandbox checkout; the existing backend container was restarted, `/health` returned healthy at `2026-05-19T19:46:48.110940+00:00`, and the frontend returned `HTTP/1.1 200 OK`.
- Live backend metadata probe after restart: `gene_extractor` and `allele_extractor` descriptions say `validator-ready identity hints`, both expose only `search_document`, `read_section`, `read_subsection`, `record_evidence`, `get_agent_contract`, and `agr_species_context_lookup`, and neither contains stale `database-assisted normalization` wording.
- Live Agent Studio validation-plan probe at `2026-05-19T19:41:13Z` against `agrmainsandbox-backend-1`:
  - `allele_extractor`: `success=True`, `domain_pack_id=agr.alliance.allele`, `allele_mention_reference_validation` appears as `validators` metadata `active`, `validator_bindings` state `active`, and has one active default-enabled attachment; `allele_pending_envelope_validator` and `source_reference_validation` appear as under-development metadata/attachments.
  - `phenotype_extractor`: `success=True`, `domain_pack_id=agr.alliance.phenotype`, `phenotype_term_ontology_validator` appears as `validators` metadata `active`, `validator_bindings` state `active`, and has one active default-enabled attachment; `phenotype.additional_provider_ontology_mappings` appears as under-development top-level planning metadata; stale `phenotype.ontology_term_resolution` is absent.

Historical full real-PDF corpus run after `558f671f` (superseded by the passing `bb135263` corpus):

```bash
python3 scripts/testing/domain_envelope_pdf_corpus.py --base-url http://192.168.86.44:8900 --allow-dev-mode-fallback --allow-duplicate-reuse --flow-timeout-seconds 900 --processing-timeout-seconds 1200
```

Script result: overall `pass`; summary artifact `docs/design/pdf-corpus-trials/summary.json` timestamp `2026-05-19T19:48:03.705766+00:00`.

Flow runs:

- Gene: `51f19373-8711-447d-8ee2-df06f30ad348`, evidence records `1`.
- Allele: `97bdbf12-b44f-4879-85fd-eb94ea6e14fb`, evidence records `1`.
- Disease: `b45ff2a4-1647-43d6-8b35-40998d01b3aa`, evidence records `1`.
- Chemical: `76a8fc8c-3d1d-45ee-8b45-977090767ea8`, evidence records `1`.
- Phenotype: `8373bcf7-f866-4700-aded-240c5b941cce`, evidence records `1`.
- Gene expression: `c2748219-0fec-4434-9c1f-7efd0f8de075`, evidence records `1`.
- Cross-domain: `fef9dd6c-e0b5-43db-b7a0-5d4543dbc78e`, evidence records `10`.

Tightened-gate interpretation: this is not an exit-criterion corpus pass yet. The helper currently passes when a flow completes with evidence, but the stricter goal requires active validator audit visibility and no hidden fallback/recovery success. Artifact inspection found `domain_validator_lookup` audit events only for allele (`6`, all `allele_mention_reference_validation`) and gene expression (`4`, `data_provider_validation` and `relation_vocabulary_validation`). Gene, disease, chemical, phenotype, and cross-domain artifacts had no `domain_validator_lookup` audit events. Disease, chemical, phenotype, gene-expression, and cross-domain runs also emitted `SPECIALIST_TEXT_FALLBACK_SUCCESS` after structured-output rejection/recovery. Backend log grep over the corpus window found no `AgentRunner.run_sync`, `validator_agent_error`, or `Package-scoped validator agent failed` messages, so the current blocker is flow/corpus gate visibility and fallback tolerance, not LinkML-requiredness relaxation or validator startup failure.

Earlier real-PDF corpus run:

```bash
python3 scripts/testing/domain_envelope_pdf_corpus.py --base-url http://192.168.86.44:8900 --allow-dev-mode-fallback --allow-duplicate-reuse
```

Result: overall `pass`; summary artifact `docs/design/pdf-corpus-trials/summary.json` timestamp `2026-05-19T16:33:09.939454+00:00`.

Earlier focused post-fix corpus rerun:

```bash
python3 scripts/testing/domain_envelope_pdf_corpus.py --base-url http://192.168.86.44:8900 --allow-dev-mode-fallback --allow-duplicate-reuse --trial cross_domain_zebrafish_segmentation_screen
```

Result after nested-term materialization fix: `pass`; latest summary timestamp `2026-05-19T17:49:42.035468+00:00`, flow run `63239102-338f-40db-9bd6-3d3f12a3dfbe`. This real-PDF pass emitted the phenotype term under the phenotype annotation; the persistence sanitizer materialized a standalone `PhenotypeTerm`, and the active phenotype validator persisted an open blocked finding for `phenotype-term-1-1` without searching MP or WBPhenotype.

Live sandbox deterministic nested-payload check against `agrmainsandbox-backend-1`:

- Input: `PhenotypeAnnotation` containing `payload.phenotype_terms[0]` label `boundary disruptions`, `ontology_lookup_hint.taxon_id = NCBITaxon:7955`, no `data_provider`, no `curie`, and no standalone `PhenotypeTerm` object.
- Sanitizer result: object types `PhenotypeAnnotation, PhenotypeTerm`; warning `materialized_nested_phenotype_terms:1`.
- Validator result: `lookup_attempts[0].method = unsupported_provider_taxon_mapping`, outcome `blocked`, finding `failure_classification = blocked`, finding status `open`; the validator runner was not called.

Live sandbox deterministic preflight check against `agrmainsandbox-backend-1`:

- Input: `PhenotypeTerm` label `boundary disruptions`, `ontology_lookup_hint.taxon_id = NCBITaxon:7955`, no `data_provider`, no `curie`.
- Result: `lookup_attempts[0].method = unsupported_provider_taxon_mapping`, outcome `blocked`, finding `failure_classification = blocked`, lookup status `blocked`; the validator runner was not called.

Sandbox validation-finding evidence from Postgres:

- `agr.alliance.allele`: 1 resolved validator finding, no open findings.
- `agr.alliance.disease`: 4 resolved validator findings, no open findings.
- `agr.alliance.chemical_condition`: 4 resolved validator findings in the chemical-only run and 4 resolved validator findings in the cross-domain run, no chemical open findings.
- `agr.alliance.gene_expression`: 2 resolved validator findings, no open findings.
- `gene`: 1 resolved validator finding in the gene-only run and 1 resolved validator finding in the cross-domain run, no open gene findings.
- `agr.alliance.phenotype`: latest focused cross-domain run has 1 open warning on `phenotype-term-1-1`, `code = domain_pack.validator_unresolved`, `failure_classification = blocked`, `lookup_attempts[0].method = unsupported_provider_taxon_mapping`, and `lookup_attempts[0].provider = domain_validator_dispatch`.

Phenotype follow-up decision after LinkML/DB review:

- The `boundary disruptions` warnings are not caused by relaxed or missing LinkML-required fields.
- Live DB inspection found no `ZP:%` ontologyterm rows and no ZP ontology term type, while the active phenotype pack intentionally supports only WB/WBPhenotypeTerm and MGI/MPTerm mappings.
- Do not resolve zebrafish phenotype labels by searching MP or WBPhenotype terms.
- Implemented fix: phenotype label validation now preflight-blocks when no active provider/taxon ontology mapping matches, preserving exact CURIE lookup but preventing unsupported ZFIN/NCBITaxon:7955 labels from drifting into mouse/worm ontology searches.
- The preflight is shared by both chat/runtime active-validator dispatch and automatic flow validation groups; the flow group path had produced the duplicate old MPTerm no-match finding before this was fixed.
- Metadata note added to the phenotype pack records the 2026-05-19 ZFIN/ZP live DB finding.

Direct gene-envelope check after `87430568`:

- Latest persisted `gene` envelopes contain only resolved `her1` and `Crumbs/crb` objects.
- `SB225002` did not persist as a curatable gene object in the latest cross-domain run.

Latest chat/workspace validation after `9c4008d5`:

```bash
python3 scripts/testing/dev_release_smoke.py --base-url http://192.168.86.44:8900 --sample-pdf /tmp/agr_domain_envelope_pdf_corpus/gene_drosophila_crb_rhabdomere.pdf --allow-dev-mode-fallback --allow-duplicate-reuse --skip-user-info --skip-flow --skip-batch --chat-model gpt-4o --chat-message 'Briefly say whether the loaded paper discusses Crumbs/crb; mention crb if present.' --chat-timeout-seconds 300 --processing-timeout-seconds 900 --evidence-dir /tmp/agr_ai_curation_chat_smoke
```

Observed result: `PASS (partial/debug run; omitted or relaxed: user_info, flow, batch, rerank_provider_smoke, dev_mode_fallback, duplicate_reuse)`.

- Evidence file: `/tmp/agr_ai_curation_chat_smoke/dev_release_smoke_20260519T171216Z.json`.
- Workspace session `b393c781-bdf8-4b83-9793-1ab3c03f57ae` had one projected domain-envelope candidate, `entity_tag_count: 0`, and passed the updated smoke helper because the workspace is projection-backed.
- Chat-runtime envelope `extraction-result:chat-runtime:f4ac2431-9818-4415-9b09-db5477917da3` revision `3` had no open validation findings.
- Backend logs for the rerun contained no `AgentRunner.run_sync`, `validator_agent_error`, or `Package-scoped validator agent failed` messages.
- Workspace prep chat answer reported Crumbs validated as `crb` / `FB:FBgn0259685` in Drosophila melanogaster with supporting evidence.

Latest chat/workspace validation after `f40c18d1`:

- Evidence file: `/tmp/agr_ai_curation_chat_smoke_f40c18d1/dev_release_smoke_20260519T190021Z.json`.
- Overall status: `pass`.
- Chat preview: the loaded paper discusses Crumbs/crb and its role in Drosophila R8 cell fate.
- Workspace-prep chat answer: `"Crumbs"` / symbol `crb` / ID `FB:FBgn0259685`, with supporting evidence quote `"Crb normally promotes pR8 cell fate through its FBM."`
- Workspace session `c048fb80-c04a-420f-8c6f-3c142f99dc52` has one projection-backed candidate from persisted envelope `extraction-result:chat-runtime:26fee1e6-77d8-4bfd-9e5a-f9f8386a8807`, object `gene-mention-evidence-crumbs-1`.
- Hydrated workspace fields include `mention = Crumbs`, `data_provider_hint = FB`, `gene_symbol = crb`, `primary_external_id = FB:FBgn0259685`, `taxon = NCBITaxon:7227`, `confidence = high`, `section = Results`, and `page = 1`.
- Backend log grep over the smoke window found `0` matches for `AgentRunner.run_sync`, `validator_agent_error`, or `Package-scoped validator agent failed`.

## Rollout Order

1. Re-run targeted tests at current HEAD and verify the deployed main sandbox has the strict-schema/audit baseline.
2. Confirm gene extraction chat shows visible validator lookup audit events and successfully resolves the selected Crumbs/crb real-PDF case in the main sandbox.
3. Done at `f1c58557`: close the cross-agent inventory gaps already identified in `docs/design/2026-05-19-gene-extractor-validator-identity-boundary.md`.
4. Add tests for each domain before moving to the next.
5. Tighten and rerun the existing real-PDF validation corpus after unit/contract tests pass.
6. Update prompts, Opus/Claude chat guidance, tool inventory, and Agent Studio/Workshop metadata after behavior is stable.

## Per-Agent Work

### Gene

- Keep extractor focused on central genes, paper evidence, species/taxon hints, and exclusions.
- Extractor may use species/taxon context lookup.
- Extractor must not use AGR gene search.
- Validator receives `mention`, `proposed_gene_id`, `proposed_symbol`, `proposed_taxon`, `taxon_hint`, `data_provider_hint`, `species`, and evidence quote.
- Validator performs AGR gene lookup and materializes authoritative `primary_external_id`, `gene_symbol`, and `taxon`.
- Audit panel must show the validator lookup payload and result.
- Tighten validator-result acceptance so resolved gene materialization requires lookup evidence or an explicit non-lookup validator classification.
- Align the gene validator prompt with the actual scalar-field materialization path; do not ask for undeclared `Gene` `resolved_objects` unless the domain pack supports them.
- Resolve the Crumbs fixture mismatch between `FB:FBgn0000368` and the real-PDF corpus result `FB:FBgn0259685`.

### Allele

- Extractor proposes allele mentions, genotypes, paper evidence, species/taxon/provider hints, and candidate gene context if paper-grounded.
- Extractor must not resolve allele IDs through AGR.
- Validator resolves allele identity, allele symbol, associated gene/provider/taxon, and reports conflicts against proposed gene/taxon context.
- Tests must cover exact allele, ambiguous allele, and genotype notation cleanup.
- `allele_pending_envelope_validator` corpus blocker was traced to lookup notation and blocked-attempt classification, not to LinkML-requiredness relaxation. Current code retries FlyBase bracket/superscript/collapsed variants and preserves blocked lookup attempts explicitly.
- Empty-selector active bindings must either become structural/non-dispatch checks or gain concrete selector inputs before they are considered passing active validators.
- Done at `f67eaddf`: active validator capability metadata now advertises `allele_mention_reference_validation`; `allele_pending_envelope_validator` and `source_reference_validation` remain under-development metadata.

### Disease

- Extractor proposes disease mentions and evidence, with organism/model context only when paper-grounded.
- Extractor must not resolve disease ontology IDs.
- Validator resolves disease term IDs/names/synonyms through the configured ontology/curation lookup path.
- Tests must cover exact disease name, synonym, ambiguous disease label, and not-found.
- Disease relation and data-provider selectors must stay required because LinkML/DTO ingest requires them; missing provider context should abstain or become visible validation work, not materialize a partial disease annotation.
- Done: replace the standalone `disease_validation` dependency on direct `curation_db_sql`; disease now uses package/runtime `agr_curation_query` helpers, and active disease bindings continue through shared package validators.
- Reconcile disease validator metadata buckets with `validator_bindings.active`, because runtime dispatch follows bindings.

### Chemical / Experimental Condition

- Extractor proposes chemical or condition mentions, dose/context evidence, and paper-grounded labels.
- Extractor must not resolve CHEBI or other chemical ontology IDs.
- Validator resolves term IDs and validates expected prefix/source where applicable.
- Tests must cover exact term, synonym/alternate label, invalid CURIE prefix, ambiguous term, and not-found.
- Chemical condition class validation should keep exact-match lookup semantics for authoritative materialization; broader fuzzy matches are useful for curator suggestions but should not silently resolve the class.
- Reconcile chemical validator metadata buckets with `validator_bindings.active`.
- Fix planned experimental-condition selector paths before promoting those bindings.

### Phenotype

- Extractor proposes phenotype statements, affected entity/context, organism/taxon hints, and supporting evidence.
- Extractor must not resolve phenotype ontology IDs unless the paper explicitly gives the ID as a proposal.
- Validator resolves phenotype/ontology terms, subject entity references, and any active reference/entity bindings declared by the domain pack.
- Pending candidate behavior must remain explicit: unresolved/pending terms should be represented as pending candidates, not fake validated terms.
- Done at `ffca8f29`: current active coverage is phenotype-term validation through `phenotype_term_ontology_validator`; the top-level validator metadata now advertises it as active. Subject and reference validators remain under development unless explicitly promoted, and additional provider/taxon ontology mappings outside WB/MGI remain under development.

### Gene Expression

- Gene expression already has active `relation_vocabulary_validation` and `data_provider_validation`.
- Extractor should propose expression observations, anatomy/stage/taxon hints, and evidence.
- Gene-expression `relation` and `data_provider` must remain required. Current converter/prompt path defaults the LinkML-required relation to the live-data-backed `is_expressed_in` vocabulary term when the observation is an expression statement, and requires the nested provider abbreviation selector when organism/provider context is known.
- Remaining validation gaps are anatomy, stage, assay, reagent, gene, and reference identity checks.
- Tests should assert no silent fake validation for gaps that do not yet have active bindings.
- Done at `de761e6b`: active validator metadata now advertises `relation_vocabulary_validation` and `data_provider_validation`; subject gene, expression-context ontology, source reference, and reagent context are listed under development with a contract test proving those fields are not silently active.
- Done at `5ccfb00b`: preserve and resolve the Agent Studio/runtime ID mismatch noted in the PDF corpus. Flow/prompt ID `gene_expression` and packaged agent ID `gene_expression_extraction` are contract-tested as equivalent for flow validation, prompt inspection, registry curation metadata, and domain-pack validation-plan inspection.

### Reference / Subject Entity / Data Provider / Controlled Vocabulary / GO / Orthologs

- Inventory whether each is a direct validator, lookup helper, or extractor-adjacent support agent.
- Apply the same contract where active validator bindings exist:
  - request built from envelope fields,
  - package-scoped validator runs,
  - result materializes authoritative fields,
  - lookup attempts are visible,
  - unresolved/planned/blocked states become findings.

## Implementation Checklist

1. Done for the LinkML-driven corpus slice: validate the current strict-schema/audit baseline at HEAD and in the main sandbox.
2. Done for commits through `36602333`: commit, push, refresh the main sandbox, restart the backend, verify the nested phenotype materialization path live, and verify no empty active validator bindings remain in domain-pack metadata.
3. Done at `f40c18d1`: re-test gene chat in the main sandbox and capture:
   - audit events,
   - backend logs,
   - final answer,
   - materialized envelope fields.
4. Done at `f1c58557`: use and update the existing inventory table in the design doc for every extractor/validator:
   - extractor agent,
   - validator agent,
   - domain pack,
   - active bindings,
   - extractor tools allowed,
   - extractor tools removed,
   - validator tools required,
   - expected materialized fields.
5. Done at `8aa4bc19`: decide sandbox document-delete behavior for domain envelopes and implement the cascade cleanup path through domain-envelope projection/history/finding/object/envelope rows.
6. Done at `de761e6b`: align gene-expression package metadata and prompt guidance so active validator ownership is limited to relation/data-provider verification and planned identity/ontology/reagent gaps remain explicit.
7. Done at `5ccfb00b`: make the `gene_expression` / `gene_expression_extraction` Agent Studio identity pair explicit in prompt/tool guidance and contract tests.
8. Done at `ffca8f29`: align phenotype active validator metadata with its active ontology-term binding and keep unsupported provider/taxon mappings as explicit under-development metadata.
9. Done at `f67eaddf`: add allele active/under-development validator capability metadata and extend the active-binding metadata guard across all active-validator top-level packs.
10. Done at `24b8c966`: add chat-runtime coverage proving the shared chat dispatch hook selects and runs active validator bindings for every launchable envelope-backed extractor with active validators.
11. Done live at `2026-05-19T19:41:13Z`: verify the deployed main sandbox `get_domain_pack_validation_plan` response exposes the corrected allele and phenotype active/under-development validator metadata.
12. Done at `37e118e9`: refresh the cross-agent design inventory so the active-binding table matches the demoted structural pending-envelope validators and current disease validator routing.
13. Done at `558f671f`: remove stale database-normalization wording from gene/allele extractor metadata and add a unit guard for extraction-safe tools plus validator-owned normalization language.
14. Done at `2878384e`: tighten the real-PDF corpus helper so script-level pass requires expected validator lookup audit events and no specialist text fallback by default; add flow-executor audit events for automatic validation groups.
15. Done locally at `819b2b2f`: preserve tightened-gate payload and validator lookup domain events even when a trial fails, so failure artifacts explain whether the blocker is missing audit, fallback, or validator errors.
16. Done at `8e7ca7e7`: align final Agent Studio static extractor descriptions and the configured supervisor prompt so extractor output uses validator-ready/proposed context while validator output owns resolved/materialized fields.
17. Done across the rollout: add or update unit tests for selector/request building.
18. Done across the rollout: add or update validator dispatch/materialization tests.
19. Done at `2878384e` and `bb135263`: add audit-event tests proving validator lookup attempts are visible and complete-flow steps cannot be skipped.
20. Done across the rollout and finalized at `8e7ca7e7`: add contract/policy tests for package metadata and prompt/tool boundaries.
21. Done: deploy to main sandbox after each coherent domain slice, then run the real-PDF corpus slice for that domain with the tightened pass/fail gate.

## Test Requirements

Default tests must not require live curation DB access. Use fakes/monkeypatches for unit and contract tests.

Required local test slices:

```bash
docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/lib/domain_packs/test_validator_dispatch.py tests/unit/lib/domain_packs/test_materialization.py tests/unit/lib/openai_agents/test_streaming_tools_helpers.py -q"
```

```bash
docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/test_gene_extractor_domain_envelope_contract.py tests/unit/lib/curation_workspace/test_extraction_results.py tests/unit/api/test_chat_stream_endpoint.py -q"
```

```bash
docker compose -f docker-compose.test.yml run --rm backend-contract-tests bash -lc "python -m pytest tests/contract/alliance/domain_packs/ -q"
```

Use integration tests when workspace persistence or API behavior changes:

```bash
docker compose -f docker-compose.test.yml run --rm backend-integration-tests bash -lc "python -m pytest tests/integration/test_curation_workspace_sessions_api.py -v --tb=short"
```

Review-requested follow-up slices:

```bash
docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/lib/curation_workspace/test_pipeline.py -q"
```

```bash
docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/test_gene_extractor_domain_envelope_contract.py tests/unit/schemas/models/test_allele_extraction_envelope.py tests/unit/test_disease_extractor_domain_envelope_contract.py tests/unit/test_chemical_extractor_domain_envelope_contract.py tests/unit/test_phenotype_extractor_domain_envelope_contract.py tests/unit/test_gene_expression_prompt_policy.py -q"
```

```bash
docker compose -f docker-compose.test.yml run --rm backend-contract-tests bash -lc "bash tests/contract/run_ci_contract_core_tests.sh --path-file tests/contract/.alliance-domain-pack-test-paths --suite-label alliance-domain-pack"
```

Optional live DB gate, only when the read-only tunnel/env is intentionally available:

```bash
docker compose -f docker-compose.test.yml run --rm backend-contract-tests bash -lc "ALLIANCE_LIVE_DB_CONTRACT_TESTS=1 bash tests/contract/run_ci_contract_core_tests.sh --path-file tests/contract/.alliance-live-db-test-paths --suite-label alliance-live-db --require-truthy-env ALLIANCE_LIVE_DB_CONTRACT_TESTS"
```

Before committing:

```bash
git diff --check
PYTHONPYCACHEPREFIX=/tmp/agr-ai-curation-pycache python3 -m py_compile <changed-python-files>
```

After deploy to the main sandbox:

```bash
python scripts/testing/domain_envelope_pdf_corpus.py --base-url http://192.168.86.44:8900 --allow-dev-mode-fallback --allow-duplicate-reuse
```

Focused gene rerun:

```bash
python scripts/testing/domain_envelope_pdf_corpus.py --base-url http://192.168.86.44:8900 --allow-dev-mode-fallback --allow-duplicate-reuse --trial gene
```

## Real-PDF Corpus Trial Plan

After each domain slice passes tests, run a sandbox trial using real uploaded PDFs, not only pasted text, abstracts, or synthetic fixtures.

Current corpus status:
- Initial real-PDF corpus results already exist in `docs/design/2026-05-19-domain-envelope-pdf-corpus-trials.md`.
- Raw per-trial artifacts already exist under `docs/design/pdf-corpus-trials/`.
- The current corpus proves upload, PDFX processing, flow execution, extraction recovery, evidence propagation, and evidence export for the selected papers.
- The latest main-sandbox full corpus run at `bb135263` passes the tightened gate for all seven trials. Every expected active validator lookup binding was observed, `SPECIALIST_TEXT_FALLBACK_SUCCESS` count is `0` in every trial, and validator problem event count is `0` in every trial.
- The latest DB findings show no open LinkML-required-field failures for allele, disease, chemical condition, gene, or gene expression.
- Allele no longer has open `validator_agent_error` lookup attempts in the latest sandbox findings.
- Disease no longer has an open missing `data_provider.abbreviation` selector in the latest sandbox findings.
- Cross-domain gene no longer persists `SB225002` as a curatable gene object in the latest sandbox findings.
- The ZFIN/NCBITaxon:7955 phenotype path now has deterministic sandbox validation and focused real-PDF evidence for both nested-term materialization and blocked unsupported provider/taxon lookup. A future stable corpus fixture could make this non-LLM-sensitive, but the runtime gap is closed.
- The corpus still does not replace chat-specific UI/audit validation; the main sandbox chat path must still be sampled and logged.
- Done at `8aa4bc19`: the sandbox document-delete behavior cascades cleanly for document-owned domain envelopes by removing regenerated domain-envelope indexes and envelope rows before deleting the PDF document.
- Historical tightened-gate failures after `558f671f` and `25687612` were resolved by the scaffold canonicalization and complete-flow corpus fixes through `bb135263`.

Paper selection rules:

- Prefer PubMed Central open-access papers with full text available.
- Record PMID, PMCID/DOI, title, organism, and target domain.
- Keep each paper’s expected curation targets small enough to inspect manually.
- Download or use the actual PDF so PDF extraction, chunking, search, evidence capture, extraction, validation, and supervisor summary all run together.
- Use at least one primary PDF per domain and at least one cross-domain PDF that exercises multiple validators in the same paper.
- Save a short trial note with:
  - prompt used,
  - PDF source URL and local/upload identifier,
  - extracted proposals,
  - validator requests,
  - validator lookup attempts,
  - materialized IDs,
  - unresolved/pending findings,
  - whether the final answer matched the envelope.

The paper search itself is part of the implementation goal. Do it during the trial phase, not in advance unless useful.

Suggested search queries:

- Gene: `Drosophila crumbs crb rhabdomere morphogenesis PMC`.
- Allele: `Drosophila allele mutant phenotype PMC FlyBase`.
- Disease: `mouse model disease gene mutation PMC`.
- Chemical/condition: `chemical treatment phenotype zebrafish PMC CHEBI`.
- Phenotype: `C elegans phenotype mutant PMC WormBase`.
- Gene expression: `zebrafish gene expression anatomy stage PMC`.

Do not rely only on the paper title. Open the full text, confirm it contains the entities needed for the specific validator type, then upload/use it in the main sandbox.

Minimum PDF corpus:

- Gene: one paper with a central gene, clear organism, and known MOD gene ID resolution.
- Allele: one paper with named alleles or mutant alleles and enough context to validate allele identity.
- Disease: one paper with a disease/model association and disease term that can be validated.
- Chemical/condition: one paper with chemical exposure, treatment, or experimental condition that can be validated against the available ontology/tooling.
- Phenotype: one paper with phenotype terms, organism context, and at least one unresolved or pending candidate case if possible.
- Gene expression: one paper with expression location/stage/entity context.
- Cross-domain: one paper that naturally contains at least two of gene, allele, phenotype, disease, chemical/condition, or expression, to prove validator dispatch composes without hiding audit events.

Tightened per-PDF pass/fail checks:

- The PDF uploads and extracts successfully.
- Extractor audit shows only allowed extractor tools for that domain.
- Extractor output uses proposal/hint fields for unvalidated identity.
- Active validator dispatch runs before supervisor summary.
- Validator audit shows request/lookup payloads and outcomes.
- Validator request/result payloads are captured in a bounded, inspectable artifact for every active binding.
- A trial cannot count as passing if it contains hidden `validator_agent_error` or `invalid_schema` lookup attempts, unless that trial explicitly targets an unresolved/failure case and the final finding is visible.
- Resolved results materialize authoritative fields into the envelope.
- Unresolved, ambiguous, pending, planned, blocked, and dispatch-unavailable cases are visible as findings when applicable.
- The final chat answer reflects validated/materialized data and does not overstate unresolved proposals.
- Backend logs have no hidden validator-agent startup failures.

## Prompt and Agent Studio Cleanup

Do this at the end, after behavior is stable.

Prompt updates:

- Extractor prompts must explain proposed-vs-validated identity.
- Extractor prompts must list allowed tools and explicitly forbid moved validation tools.
- Validator prompts must explain `DomainValidationRequest`, selected inputs, expected result fields, lookup attempt reporting, and resolved/unresolved criteria.
- Supervisor/chat prompts must explain that envelope-backed extractors may run validators internally before the supervisor sees the result.
- Opus/Claude “Chat with Claude” knowledge must explain the runtime architecture so external review does not confuse extractor proposals with validated materialized fields.
- Done at `8e7ca7e7`: the configured supervisor prompt no longer says gene extraction returns `normalized IDs` or performs `evidence and normalization`; it now describes validator-ready identity context and validator findings when active.
- Update the canonical Agent Studio system prompt and its fallback/test copy:
  - `alliance_config/agent_studio_system_prompt.md`
  - `backend/src/api/agent_studio_system_prompt.md`
- Update associated prompt-policy tests, especially Agent Studio domain-envelope prompt guardrails, so stale “extractor calls validators directly” assumptions cannot reappear.

Agent Studio / Workshop updates:

- For every agent, show curator-facing tool inventory:
  - tools this agent can use,
  - tools deliberately unavailable,
  - whether it reads the paper,
  - whether it validates against curation DB/ontology,
  - what fields it proposes,
  - what fields it materializes.
- Ensure Chat with Claude has matching tool knowledge and usable tools for the new architecture:
  - Opus tool definitions and tab scoping in `backend/src/api/agent_studio_opus_tools.py`.
  - Domain-envelope inspection tools in `backend/src/lib/agent_studio/domain_envelope_tools.py`.
  - Package diagnostic tool registration in `backend/src/lib/agent_studio/diagnostic_tools/tool_definitions.py`.
  - Tool library / policy metadata surfaced through Agent Workshop and Prompt Workshop.
- Add or expose a first-class read-only Chat with Claude tool inventory/details path, such as `get_tool_inventory` and `get_tool_details`.
- Make `agr_species_context_lookup` and other extractor-safe tools visible as metadata to Claude even when they are not executable diagnostic tools.
- Done: frontend `OpusChat` lookup rendering formats current `agr_curation_query` method-specific payloads such as `method`, `gene_symbol`, `gene_id`, `data_provider`, and ontology term fields curator-readably instead of falling back to raw JSON.
- Ensure Agent Workshop context steers Claude to call `get_domain_pack_validation_plan` before answering automatic-validator/materialization questions about a draft.
- Add workshop/help text describing extractor vs validator responsibilities.
- Make validator lookup visibility clear in the audit panel documentation.
- Done at `8e7ca7e7`: static Agent Studio extractor documentation for gene, allele, disease, chemical, phenotype, and gene expression no longer claims extractors perform database/ontology normalization. A unit guard now prevents those stale boundary phrases from returning.

## Acceptance Criteria

- Gene validator dispatch truly performs AGR lookup in chat and the audit panel shows the lookup payload/result.
- No extractor uses database/entity lookup tools that have been moved to validation.
- Every active validator binding either resolves, produces an unresolved finding, or produces an explicit dispatch-unavailable/planned/blocked finding.
- No “complete” audit event hides validator-agent startup failures.
- Every domain has tests for success and at least one unresolved path.
- Every domain has at least one real-PDF sandbox trial note.
- Design doc, prompts, Opus/Claude guidance, and Agent Studio/Workshop metadata reflect the final architecture.
- Final state is committed, pushed, and deployed to the main sandbox.

## Exit Goal

This project is complete only when all of the following are true:

1. Every envelope-backed extractor with active domain-pack validator bindings has an explicit documented tool boundary, request selector, materialization target, lookup-attempt visibility policy, and tests.
2. Extractor prompts, validator prompts, domain-pack metadata, Chat with Claude knowledge/tools, and Agent Studio/Workshop descriptions agree on that boundary.
3. Under-development bindings are listed separately with promotion blockers and are not treated as required resolved validators unless explicitly promoted.
4. Chat-time extractor output is validated/materialized before supervisor summarization for gene and either verified for every other launchable envelope-backed extractor with active validators or explicitly scoped to Agent Studio flow execution for this exit.
5. The audit panel and Chat with Claude tools show enough validator request/lookup/result detail to debug failures without reading backend logs first.
6. Unit and contract tests cover success plus at least one unresolved/failure path for each active-validator domain.
7. Any integration tests required by workspace persistence or chat API behavior pass.
8. A real uploaded-PDF corpus has been run in the main sandbox, with trial notes for every domain and one cross-domain paper, under the tightened pass/fail gate.
9. No hidden validator startup failures appear in backend logs during the PDF corpus run.
10. All changes are committed, pushed to `origin/main`, deployed to the main sandbox, and verified live there.
11. The final handoff states the exact commit SHA, sandbox HEAD, tests run, PDF corpus papers used, unresolved expected failures if any, and any deliberately remaining limitations.

## Completion Audit: 2026-05-19

Deliverables / success criteria checked against artifacts:

- LinkML and live curation DB requiredness: checked against pinned `agr_curation_schema` commit `1b11d0888f19eba4ca72022200bb7d96b30d4a52` plus read-only curation DB counts. Evidence is recorded above in `2026-05-19 LinkML requiredness check`; no LinkML-required disease relation/data-provider, gene-expression relation/data-provider, condition-relation type, or allele taxon requirement was relaxed.
- Extractor/validator boundary across envelope-backed domains: implemented through the commits listed above, with package metadata, prompt, domain-pack, and dispatch tests covering gene, allele, disease, chemical condition, phenotype, and gene expression. Final prompt/static doc cleanup is `8e7ca7e7`.
- Under-development versus active validators: active validator metadata and active bindings are contract-tested across allele, chemical, disease, gene expression, and phenotype. Structural pending-envelope checks remain under development instead of being treated as active runtime validators.
- Chat-time and flow-time validator dispatch: chat dispatch is unit-covered for every launchable extractor with active validators, and flow execution now treats configured step tools as required before completion.
- Audit and debugging visibility: flow validation emits `domain_validator_lookup` audit events, `get_domain_envelope_state` exposes bounded validator summaries, and Chat with Claude has `get_tool_inventory` / `get_tool_details` / validation-plan diagnostics.
- Real uploaded-PDF corpus: `docs/design/pdf-corpus-trials/summary.json` timestamp `2026-05-19T21:19:59.332670+00:00`, `overall_status=pass`, seven results. Trials covered gene, allele, disease, chemical condition, phenotype, gene expression, and cross-domain zebrafish segmentation. Every expected active validator binding was observed, `missing_expected_validator_bindings=[]`, `SPECIALIST_TEXT_FALLBACK_SUCCESS=0`, and validator problem event count `0` in every trial.
- Backend/sandbox evidence: local `HEAD` and `origin/main` are `8e7ca7e7b4342701f05d9cd12d6ce7fbb446c551`; VM source checkout and main sandbox checkout are the same SHA. `agrmainsandbox-backend-1` was directly restarted at `2026-05-19T21:39:33.447390174Z`, Docker health is `healthy`, and `/health` returned healthy at `2026-05-19T21:41:46.848749+00:00`.
- Hidden failure audit: backend logs since the `8e7ca7e7` restart have no matches for `Traceback`, `ERROR`, `validator_agent_error`, `SPECIALIST_TEXT_FALLBACK_SUCCESS`, `AgentRunner.run_sync`, or `incomplete_flow_steps`. Logs after the `bb135263` corpus run likewise had no validator startup/fallback failures.
- Prompt/static wording audit: stale source prompt/doc phrases such as `database-assisted normalization`, `Alliance database normalization`, `Disease ontology normalization`, `ChEBI normalization`, `normalized IDs`, and `Gene assertions/normalized` are absent from the configured supervisor prompt, static Agent Studio extractor docs, Alliance package prompts, and Agent Studio prompt knowledge except where listed as forbidden strings in regression tests.
- Final validation commands: latest focused prompt/static cleanup tests passed with `29 passed, 1 warning`; complete-flow tests passed with `6 passed`, `87 passed`, and `27 passed` slices; the full corpus passed in the main sandbox as above.
- Workspace note: generated corpus artifacts under `docs/design/pdf-corpus-trials/*.json` remain locally modified as preserved evidence from the passing corpus run, and `goal.md` remains the local workpad update requested in this thread. Code/config/test changes are committed and pushed through `8e7ca7e7`.
