# Custom output structures

Use **Agent Workshop → Output Structure** to describe the records your agent should extract. You do not need to write JSON.

First decide what one record represents. For example, “one reagent per paper” differs from “one record for every mention.” Tell AI Chat which you mean; it can propose a first draft for you to review.

## Choose an output mode

- **No structured output** suits conversational or tool-only agents.
- **Typed domain output** uses a packaged record format. Check its stated fields, validators and export capabilities; an in-development format may still fit your task.
- **Custom structured generic output** uses your saved, closed profile: only declared fields are allowed. It is not automatically LinkML-aligned or submission-ready.
- **Flexible generic output** permits exploratory attributes without a custom closed profile. Choose this explicitly; clearing a packaged schema does not enable it.

## Build and review fields

Give each field a clear name and select text, number, boolean, controlled choices, a list, or a group. Use repeating groups when values must stay paired, such as source names and identifiers.

**Required** means a field must exist. **Nullable** means its value may be unknown (`null`). These are different: a required nullable field must appear, but may contain `null`. An optional field may be absent. Do not supply an invented value merely to satisfy a required field.

**Synonyms / source labels (not output fields)** help recognize wording in papers. They do not create alternate output keys. For example, the recognition hint “synonym” can point to `paper_labels`; the extracted output still uses `paper_labels`.

AI Chat proposals appear in the shared review dialog. **Cancel** leaves the draft unchanged; **Apply** changes only the unsaved draft. A stale proposal must be regenerated. You can undo the last accepted change while the draft still matches it. Only your separate **Save** creates a saved revision.

## Understand validation and revisions

Every custom profile enforces its declared structure. Semantic validation is separate and optional: it requires an explicitly mapped, compatible packaged capability. A field without a mapping is structurally checked, not semantically verified. Do not assume reagent names or stock identifiers have a validator just because the field has a familiar name.

Saved agents and flow nodes retain exact revision identities. Editing an agent or profile later does not silently update an existing flow node. Select the new revision explicitly and reverify the flow when you want it to change. Review the saved output and validation findings before exporting.

## Provisional dev example: reagent inventory

The developer fixture `backend/tests/fixtures/profiles/provisional_reagent_inventory.json` is synthetic and **not curator-approved**. It assumes one reagent per document, exact paper labels in first-evidence order with duplicates preserved, and paired source entries. Source states are `new_in_paper`, `external`, and `not_stated`.

The two-column example uses existing list/pair joins and a conditional display rule. “New in paper” is display text, never a stored source identifier. Source-only entries display the name; identifier-only entries display the identifier. The not-stated example has no source values and therefore displays an empty cell. Contradictory status/source combinations need curator review; the fixture does not introduce a business-rule validator or silently discard those values.

Before using this for real curation, confirm record grouping, label meaning and duplicate treatment, source pairing, identifier-only behavior, source-state meanings, ordering, and representative expected rows. These choices remain pending human review. Test only a clearly named dev clone; do not replace or retire the original production flow.
