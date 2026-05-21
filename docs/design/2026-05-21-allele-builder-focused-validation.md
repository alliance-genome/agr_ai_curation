# Allele Builder Focused Validation

Date: 2026-05-21

Scope: allele-first builder-tool extraction path.

Deployed commit:

- `2165fda3cd5900cf1e5cca6f30ea5ae10ac2e37f`
- Branch: `live-multi-gene-validator-batching`
- Main sandbox backend: `http://192.168.86.44:8900`

Focused corpus command:

```bash
python3 scripts/testing/domain_envelope_pdf_corpus.py \
  --base-url http://192.168.86.44:8900 \
  --allow-dev-mode-fallback \
  --allow-duplicate-reuse \
  --trial allele \
  --flow-timeout-seconds 900 \
  --processing-timeout-seconds 1200 \
  --output-dir docs/design/pdf-corpus-trials/allele-builder-20260521-1402
```

Focused corpus result:

- Artifact directory: `docs/design/pdf-corpus-trials/allele-builder-20260521-1402/`
- Trial: `allele_drosophila_notch_facet_glossy`
- Overall status: `pass`
- `stage_allele_paper_evidence` complete count: `1`
- `finalize_allele_extraction` complete count: `1`
- Builder staged/finalized counts: `1` / `1`
- Backend finalized object count: `4`
- Builder validator target count: `1`
- Zero-validator-job status: `validator_jobs_executed`
- Observed validator binding: `allele_mention_reference_validation` count `2`
- Specialist text fallback events: `0`
- Validator problem events: `0`

Additional validation:

- Targeted backend unit suite: `121 passed`
- Focused backend contract suite: `25 passed, 1 deselected`
- `git diff --check`: clean

The broader full real-PDF corpus was not used as allele-first acceptance evidence. A full corpus sweep remains a separate regression/release gate.
