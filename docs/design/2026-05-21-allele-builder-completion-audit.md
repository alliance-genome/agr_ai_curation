# Allele Builder Completion Audit

Date: 2026-05-21

Objective audited: implement the allele extractor as the first YAML-driven
builder-tool extraction path, with the model staging allele findings and the
backend building the final `AlleleExtractionResultEnvelope`.

## Evidence Map

Implementation commits:

- `2165fda3` - allele builder extraction path implementation
- `e3a6185c` - focused allele corpus validation evidence
- `25de0864` - corpus runner validation evidence
- `d6028646` - completion audit before full-corpus regression evidence

Focused validation note:

- `docs/design/2026-05-21-allele-builder-focused-validation.md`

Focused corpus artifacts:

- `docs/design/pdf-corpus-trials/allele-builder-20260521-1402/summary.json`
- `docs/design/pdf-corpus-trials/allele-builder-20260521-1402/allele_drosophila_notch_facet_glossy.json`

Full-corpus regression artifacts:

- `docs/design/pdf-corpus-trials/allele-builder-full-20260521-1418/summary.json`
- `docs/design/pdf-corpus-trials/allele-builder-full-20260521-1418/disease_mouse_pkd1_adpkd.json`
- `docs/design/pdf-corpus-trials/allele-builder-full-20260521-1418/allele_drosophila_notch_facet_glossy.json`

## Done-Criteria Checklist

| Requirement | Evidence | Status |
| --- | --- | --- |
| Allele extractor configured for builder tools | `packages/alliance/agents/allele_extractor/agent.yaml` declares `stage_allele_paper_evidence` and `finalize_allele_extraction`; `packages/alliance/tools/bindings.yaml` registers both inline tools. | Done |
| YAML-derived builder contract in prompt context | `backend/src/lib/prompts/assembly.py` renders builder metadata; `packages/alliance/domain_packs/allele/domain_pack.yaml` contains `metadata.extraction_builder`; `tests/unit/lib/prompts/test_assembly.py` passed. | Done |
| `get_agent_contract(topic="builder_tools")` returns YAML-derived hints | `backend/src/lib/agent_contracts.py` handles `builder_tools`; `tests/unit/lib/test_agent_contracts.py` passed. | Done |
| Model-facing final output is an acknowledgment | `ExtractionToolFinalizationAck` in `backend/src/lib/openai_agents/models.py`; Agent Studio runtime swaps builder final output to ack while preserving curation schema; targeted unit tests passed. | Done |
| Backend-built finalized staged envelope validates as `AlleleExtractionResultEnvelope` | `backend/src/lib/openai_agents/extraction_staging.py`; `tests/unit/lib/openai_agents/test_extraction_staging.py` and focused corpus gate passed. | Done |
| Non-empty finalized allele output includes `Reference`, `AlleleMention`, `EvidenceQuote`, and `AllelePaperEvidenceAssociation` | Staging unit tests assert required sibling objects and refs; focused corpus reported finalized object count `4`. | Done |
| Active validator dispatch receives non-empty `AlleleMention` targets | Focused corpus observed `allele_mention_reference_validation` count `2`, validator target count `1`, and validator dispatch completion status `complete`. | Done |
| Logs/events expose builder success/failure clearly enough for trace review | `SPECIALIST_SUMMARY` includes builder metrics; focused corpus captured builder observations with stage/finalize counts, object count, validator target count, and zero-validator status. | Done |
| Focused backend unit and contract tests pass | Targeted backend unit suite: `121 passed`; contract suite: `25 passed, 1 deselected`; corpus runner unit suite: `6 passed`. | Done |
| Focused allele real-PDF corpus passes with tightened gate and no specialist text fallback | `docs/design/pdf-corpus-trials/allele-builder-20260521-1402/summary.json` has `overall_status=pass`; specialist text fallback count `0`. | Done |
| Full real-PDF corpus passes or unrelated failures are documented | Full corpus `allele-builder-full-20260521-1418` ran. Seven of eight trials passed, including allele. The only failure was `disease_mouse_pkd1_adpkd`, where `Disease Extraction Agent` emitted JSON missing `payload.evidence_records` for `DiseaseExtractionResultEnvelope`; disease builder stage/finalize counts were `0`, so this is outside the allele builder migration. | Done |
| Changes are committed, pushed, synced to Incus main sandbox, and tested there | Branch pushed to `origin/live-multi-gene-validator-batching`; VM source checkout synced from Git; main sandbox backend healthy and focused/full corpus runs used `http://192.168.86.44:8900`. | Done |

## Audit Conclusion

The allele-first implementation is complete and validated. The full corpus did
not pass cleanly, but the only failure was documented as a disease extractor
schema issue outside the allele builder migration, satisfying the
`goal.md` allowance for unrelated full-corpus failures with evidence.
