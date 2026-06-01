# Per-element validation for multivalued fields (retire the `[0]` convention)

Date: 2026-06-01. Status: **DESIGN — approved by Chris 2026-06-01; not yet implemented.**
Origin: surfaced while landing R4 (disease `with_or_from` / `disease_qualifiers`). Chris: "we definitely
need full validation … from here onward … not something we can gloss over."

## Problem

A validatable field that holds a **list** is only validated at index `[0]`. Elements `[1:]` are staged,
materialized, and exported but **never sent to a validator** — silent under-validation. A disease finding
with three ECO evidence codes gets one ontology-checked and two waved through. There is also a latent
crash edge: if a lookup canonicalizes element 0 to a value different from what was staged, the write-back
raises (see "scalar-only writer" below).

This is NOT cosmetic and NOT disease-specific. It is a core validation-engine gap that affects every pack.

### Why it's `[0]`-only today (the two hard constraints)
1. **One match per field_path.** `DomainPackValidationRegistry.match_bindings`
   (`backend/src/lib/domain_packs/validation_registry.py:492`, the field loop at `:531-549`) emits exactly
   one `ValidatorBindingMatch` per matched `field_definition`. A binding validates one `(binding, object,
   field_path)` target. There is no notion of "run this binding once per list element."
2. **The payload writer refuses list indices.** `_set_payload_value`
   (`backend/src/lib/domain_packs/materialization.py:1077-1094`) raises `DomainEnvelopeMaterializationError`
   if any path part or the leaf is an integer index ("paths cannot create list indexes" / "cannot end with a
   list index"). All validator write-back (`expected_result_fields`) and all `materializes_to_field_paths`
   mirrors go through this scalar-only writer. Reading an index is fine (`_payload_value`/`parse_field_path`
   handle `field[0]`); WRITING one is forbidden.

So the platform can validate/resolve at most ONE fixed scalar slot per binding. The workaround was to declare
fields with a literal index, e.g. `field_path: evidence_code_curies[0]`, pinning validation to position 0.
The in-place write-back avoids the raise only because of the equality guard at
`materialization.py:441-443` (if the resolved value already equals the staged value, the write is skipped).

## Where it bites (survey, 2026-06-01)

DB-validated multivalued fields declared with the `[0]` convention:

| Field | Pack | Validated against | Multi in practice? |
|---|---|---|---|
| `evidence_code_curies` | disease | ECO ontology (`disease_evidence_code_lookup`) | **YES, routinely** (2-3 codes) — the real motivation |
| `disease_qualifier_names` | disease | "Disease Qualifier" CV (`disease_qualifier_cv_lookup`) | rare (>1 uncommon) |
| `with_gene_identifiers` | disease | gene entity (`disease_with_gene_validation`) | sparse (0-1) |
| `phenotype_terms` | phenotype | HP/MP ontology (`phenotype_term_ontology_validator`) | **No** — builder stages 1 term/annotation (length-1 list) |

NOT DB-validated (the limitation does not apply — internal lists, no external lookup):
`source_mentions`, `evidence_record_ids`.

Takeaway: `evidence_code_curies` is the field that actually bites today and the best proof case.

## Data structure (for reference)

Shape A — list of scalars (`evidence_code_curies`, `disease_qualifier_names`, `with_gene_identifiers`):
```yaml
evidence_code_curies: [ECO:0000315, ECO:0000316]   # payload holds the full array
```
Shape B — list of objects (`phenotype_terms`): each element is `{curie, label, source_mentions: [...]}`.
The pack declares the validatable leaf with an index: `field_path: evidence_code_curies[0]` (Shape A) or
`phenotype_terms[0].curie` (Shape B). The payload is N-wide; the validation wiring names position 0.

## Decisions (Chris, 2026-06-01 — all approved)
- **D1 Declaration:** a field marks itself **`multivalued: true`** in its metadata (bare `field_path`, NO
  fake `[0]`). Preferred over a `field[*]` literal.
- **D2 Engine-generic:** the fix lives in `backend/src` (the validation engine) — NO domain logic. Every pack
  and every future field inherits it. Consistent with §5.
- **D3 Migration order:** build the engine generically, then migrate **`evidence_code_curies` FIRST** as the
  test/proof, then fold in `disease_qualifier_names` + `with_gene_identifiers`, and **retire the `[0]`
  convention** everywhere it's used for DB-validated lists.
- **D4 Curator edits:** when a curator patches in a new element, **re-validate** the new element (re-dispatch
  on patch).
- **D5 Batching:** N elements of one field become **one batched validator call** via the existing batch
  machinery — fan-out must NOT multiply LLM/tool calls.
- **D6 Findings:** **per-element** findings (one per list item, `field_ref → field[i]`), so a curator sees
  which specific code/qualifier/gene resolved or failed.

## Design — five engine pieces (with exact anchors)

The engine change is **additive**: fan-out triggers ONLY on `multivalued: true`. Existing `[0]`-literal
fields are untouched until each is migrated, so there is no big-bang and migration is per-field.

1. **Declaration + schema (D1).** Add `multivalued: true` as recognized field metadata (validate it in the
   domain-pack metadata schema; only valid on validatable fields). The pack declares `field` (bare) with
   `multivalued: true` instead of `field[0]`. Binding `field_paths`, the input-selector `path`, and
   `expected_result_fields` all reference the bare `field` (the engine supplies the per-element index).
2. **Match fan-out (the core new logic).** In `match_bindings`
   (`validation_registry.py:531-549`), when a matched `field_definition` is `multivalued`, read the payload
   list at `object_envelope.payload[base_field]` and emit **one `ValidatorBindingMatch` per present element**
   (indices `0..n-1`), each carrying the element index. Add an `element_index: int | None` (or a resolved
   `indexed_field_path`) to `ValidatorBindingMatch` (`validation_registry.py:287+`). Empty/absent list → 0
   matches (nothing to validate; if the field is required, the existing required-field structural check still
   fires on the base field). This is the single fan-out point.
3. **Per-element request.** `build_domain_validation_request` (`input_selectors.py:54`) already builds a
   request from a match; for a fanned-out match it resolves selector `path: field` against the element index
   (`field[i]`) and sets the `expected_result_fields` target to `field[i]`. Mechanically minimal —
   `_value_at_path` already reads indexed paths.
4. **Index-capable write-back (the one real lift).** Generalize `_set_payload_value`
   (`materialization.py:1077`) to write `field[i]`: walk/extend lists for int path parts instead of raising;
   set the indexed slot. Update `_materialized_field_path` (`materialization.py:736`) to accept `field[i]`
   when the base `field` is a declared `multivalued` field, and `_propagate_materialized_mirror_paths`
   (`:752`) to mirror per element if ever needed. Scalar paths (no int parts) are unaffected — pure
   generalization. Keep the equality guard (skip write when resolved == staged) per element.
5. **Per-element findings (D6).** Falls out of #2 — each match yields its own finding with
   `field_ref → field[i]`. No extra mechanism; verify the finding's `field_path`/`field_ref` carries the
   index so the UI can point at the right element.

**Batching (D5).** The fanned-out matches share the binding's `batch_family` (`validation_registry.py:129-131`,
`validator_dispatch.py`), so they group into one batched validator call. **Verify each relevant validator
agent (ontology / controlled_vocabulary / gene) has a batch run path**; if one lacks it, fan-out still works
(N calls for that validator) until batching is added — note any gap here when implementing.

## Backward-compat & migration
- Engine ships first as additive (no behavior change for existing fields). Then per-field migration: flip the
  field declaration from `field[0]` to `field` + `multivalued: true`, and flip the binding's `field_paths` /
  selector `path` / `expected_result_fields` from `field[0]` to `field`.
- **Migrate `evidence_code_curies` first** (disease) — the real pain + best proof. Then
  `disease_qualifier_names` + `with_gene_identifiers`. `phenotype_terms` is single in practice; migrate it for
  consistency if cheap, else leave. `source_mentions`/`evidence_record_ids` are NOT DB-validated — leave them.
- Single-element lists must behave identically before/after migration (regression check).

## Edge cases / risks
- Empty list → 0 matches (correct). Required-but-empty still caught by the existing required-field check.
- Curator removes/reorders/adds elements (patch) → re-dispatch re-fans-out over the new list (D4).
- Per-element equality guard + mirrors must hold (don't double-write unchanged slots).
- `batch_max_size` bound — a pathological 50-element list should chunk per the existing batch max.
- Element identity is positional (index). A reorder re-validates; acceptable (validation is idempotent).
- Validators without a batch path → graceful fallback to N calls (note + ticket if it matters for cost).

## Test matrix
- **Unit:** fan-out emits N matches for an N-element `multivalued` field; `_set_payload_value` writes/extends
  `field[i]`; `_materialized_field_path` resolves `field[i]`; per-element findings carry the index; empty list
  → 0 matches; non-multivalued fields unchanged.
- **Contract:** a pack field with `multivalued: true` + a 2+-element payload → all elements validated;
  `evidence_code_curies` post-migration validates every code.
- **E2E (sandbox):** a disease finding with **2+ ECO codes** → both ontology-validated (not just the first);
  0 structural findings; per-element findings visible. Use the AD paper or a crafted multi-code case.

## Gate (same rigor as R3/R4)
Branch → engine + evidence_codes migration → deploy to sandbox → unit/contract + broad suite green → e2e
proving multi-element validation end-to-end → independent Opus review → report before landing. Then expand to
the remaining fields and retire `[0]`.

## Status log
- 2026-06-01: design approved by Chris; doc written.
- 2026-06-01: FIRST PASS (proof) LANDED — generic engine (all 5 pieces) + `evidence_code_curies` migrated.
  Broad suite 568 passed (+16, 0 regressions); my review + an independent Opus review both CLEAN; sandbox e2e
  PASS (0 structural regression; fan-out proven against the LIVE deployed pack/engine: 1/2/3-element lists ->
  1/2/3 per-element matches + requests at evidence_code_curies[0..n-1], 0->0; in-container unit suite 6/6). The
  AD paper yielded 0 ECO codes that run, so the live LLM->ontology multi-code path is covered by the deployed
  pack/engine run + the dispatch->materialize unit test, not a real 2-code PDF. Also flipped export.py
  `_REQUIRED_DISEASE_FIELD_PATHS` `evidence_code_curies[0]` -> bare (semantically identical; `bool([])` False).
- FOLLOW-UPS for the EXPANSION pass (review-confirmed, not blocking the proof):
  * BATCHING (D5): `disease_evidence_code_lookup` is NOT batch-enabled, so a multi-ECO finding currently fans
    out to N ontology calls, not one batched call. The ontology validator DOES support batch
    (agent.yaml batchable:true); enabling is a one-line `batch: {enabled: true, family: ...}` block on the
    binding. MUST wire before production use (else fan-out multiplies validator calls). Do it when migrating
    each field.
  * PER-ELEMENT MIRRORS: `_propagate_materialized_mirror_paths` no-ops for an indexed path
    (`field[i]` not in declared_fields). Harmless today (no multivalued field declares
    `materializes_to_field_paths`), but a future multivalued field WITH mirrors would silently skip per-element
    mirroring — generalize the mirror writer if/when that case arises.
- NEXT: land the proof pass, then EXPAND — migrate `disease_qualifier_names` + `with_gene_identifiers` (and any
  other DB-validated `[0]` field), enable batching per binding, retire `[0]` everywhere.
