# Custom output structures

Use **Agent Workshop → Output Structure** to describe the records your agent should extract. You do not need to write JSON.

First decide what one record represents. For example, “one reagent per paper” differs from “one record for every mention.” Add a brief description under **Additional guidance for this item type**. The extraction AI receives this saved description in addition to the agent prompt and individual detail instructions. Use it to clarify what to include, exclude, and treat as a separate record; you do not need to repeat your full prompt. AI Chat can also help you draft it.

## Work with AI Chat

AI Chat reads the current Workshop draft, including unsaved manual edits, detail names and parts. You can ask it to add a supplier name and stock number to the same answer, rename a detail, change its answer format or choices, edit extraction instructions, or change **Always include** and the empty-answer setting. It can also remove or reorder details and parts.

For example: “Under Stock details, add Stock number as text and always include it. Keep Supplier name optional.” AI Chat proposes changes to that group while keeping unrelated settings and your earlier agent prompt. Review the changes and choose **Apply** to update the open editor. **Save** saves the agent separately. If you edit the draft while a proposal is pending, ask for a fresh proposal so your newer changes are preserved.

The AI follows the same simple design: one item type, one answer per detail, and simple parts within a group. It preserves existing list or deeper-group definitions during unrelated edits and explains any proposed format conversion.

## Choose an output mode

- **No structured output** suits conversational or tool-only agents.
- **Typed domain output** uses a packaged record format. Check its stated fields, validators and export capabilities; an in-development format may still fit your task.
- **Custom structured generic output** uses your saved, closed profile: only declared fields are allowed. It is not automatically LinkML-aligned or submission-ready.
- **Flexible generic output** permits exploratory attributes without a custom closed profile. Choose this explicitly; clearing a packaged schema does not enable it.

## Build and review fields

Give each detail a clear name and choose text, a number, yes/no, controlled choices, or an answer with several parts. The simplified editor collects one answer per detail: for example, one supplier with one catalog number. The parent detail shows a **Parts of …** table, including headers before any parts are added. Use **Add another part** to put a second detail in the same answer. Select **Always include** for each part that must be present whenever the parent answer is included. Each part uses a simple answer format, such as text, a number, or a choice; parts cannot contain further groups of parts. The editor does not offer a multiple-answer control. Previously saved list formats remain unchanged unless you explicitly convert them to one answer; existing extraction results are not rewritten.

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
