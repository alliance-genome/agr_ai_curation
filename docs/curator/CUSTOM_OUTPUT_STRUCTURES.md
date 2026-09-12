# Custom output structures

Use **Agent Studio → Agent Workshop → Custom data extraction** to describe what your agent should collect from a paper. The wizard supports one item type per agent. You define the questions here; the agent fills in answers when it runs on a paper. You do not need to write JSON.

First decide what one record represents. For example, “one record per distinct reagent used in the paper” differs from “one record for every mention.” Add a brief description under **Additional guidance for this item type**. The extraction AI receives this saved description in addition to the agent prompt and individual detail instructions. Use it to clarify what to include, exclude, and treat as a separate record; you do not need to repeat your full prompt. AI Chat can also help you draft it.

## Preview, edit, and return to your draft

Start with **Agent Workshop → Custom data extraction**. Setup shows a read-only table of your details, their parts, inclusion rules, and attached validators. Choose **Add details to collect** for an empty structure or **Edit details to collect** above a populated table to edit that same structure, then **Back to Setup** to return. Browser Back also follows the Workshop sections and detail pages while keeping your edits.

Unsaved agent and flow drafts are kept on this browser for your signed-in account, including unfinished flow-step instructions. After returning or reloading, choose **Resume draft** or **Discard draft**. This recovery is local to your device; use **Save** to save the agent or flow to your account. If browser storage is unavailable, a warning tells you to keep the page open and Save.

Recovered flows open as unsaved copies. If a saved agent changed since your draft was kept, its recovery opens as a new agent too. Shared-agent clones require their source to remain available. These safeguards preserve saved work while you review recovered edits.

## Work with AI Chat

AI Chat reads the current Workshop draft, including unsaved manual edits, detail names and parts. You can ask it to add a supplier name and stock number to the same answer, rename a detail, change its answer format or choices, edit extraction instructions, or change **Always include** and the empty-answer setting. It can also remove or reorder details and parts.

For example: “Under Stock details, add Stock number as text and always include it. Keep Supplier name optional.” AI Chat proposes changes to that group while keeping unrelated settings and your earlier agent prompt. Review the changes and choose **Apply** to update the open editor. **Save** saves the agent separately. If you edit the draft while a proposal is pending, ask for a fresh proposal so your newer changes are preserved.

AI Chat can also help with the agent name, description, icon, model and reasoning, sharing, tools, and main or group instructions. It guides you through the relevant sections and can offer to keep suitable defaults. Each message reads the current editor, including your manual edits. During an existing chat, opening a draft through the chat action or successfully saving it lets the conversation continue once its settings finish loading. Canceling Save or a failed save does not start a follow-up.

The AI follows the same simple design: one item type, one answer per detail, and simple parts within a group. It preserves existing list or deeper-group definitions during unrelated edits and explains any proposed format conversion.

## Choose an output mode

New drafts from the General PDF Extraction template start with **Custom Output Structure** and **GPT-6 Astra with low reasoning**. AI Chat uses Astra with medium reasoning; validation agents keep their Terra settings. Saved agents and flow revisions keep their existing model choices. Opening or cloning a saved agent keeps its saved output choice. Each format has an explanation directly below the selector.

- **Custom Output Structure** defines consistent details, answer types, and inclusion rules across papers. Attach supported validators to details or parts when needed. It is not automatically ready for Alliance submission.
- **Flexible extraction** lets the agent choose useful fields while reading. Fields may vary between runs, and custom-field validators are not applied. It suits exploratory chat or CSV, TSV, and JSON exports when fixed columns are unnecessary; general record and evidence rules still apply.
- **Packaged domain format** uses an existing biological structure, with automatic validation where supported and enabled. Inspect its supported fields and capabilities; choose custom output if you need additional fields.
- **No structured output** suits conversational or tool-only agents.

## Build and review fields

Choose **Add a detail**, enter its name, and choose an answer format:

| Answer format | Example |
|---------------|---------|
| Text | Stock name: Canton-S |
| Whole number | Number of animals: 12 |
| Decimal number | Reported fold change: 2.5 |
| Yes or no | Generated by the authors: yes |
| Choose from a list | One of: newly made, obtained elsewhere, not stated |
| An answer with several parts | A source with a Supplier name and Catalog number |

For a choice list, enter one allowed choice per line. Add **Instructions for this detail** when its name alone does not explain what to collect. Choose **Done** when ready; edits remain in the draft until Workshop Save.

The simplified editor collects one answer per detail: for example, one supplier with one catalog number. The parent detail shows a **Parts of …** table, including headers before any parts are added. Use **Add the first part**, then **Add another part**, to put details in the same answer. After adding a part, you stay with the parent answer so you can add the next one. Select a part to edit it and use its Done button to return to the parent. Select **Always include** for each part that must be present whenever the parent answer is included.

Each part uses a simple answer format, such as text, a number, or a choice; parts cannot contain further groups of parts. The editor does not offer a multiple-answer control. Previously saved list formats remain unchanged unless you explicitly convert them to one answer; existing extraction results are not rewritten.

### Include a detail, or allow it to be empty

**Ask for this in every record** requires the detail to appear. In a parts table, **Always include** requires that part whenever its parent answer appears. The parts already belong together because they are in the same answer; this setting does not pair them.

For example, make **Source** optional, then give it two parts: **Supplier name** and **Catalog number**. If Catalog number has **Always include** selected, a Source answer must contain that part. Supplier name may still be omitted if its checkbox is clear. If Source is absent altogether, neither part is required. Use the **(?)** beside Always include to read the explanation in the editor.

Under **More field options**, **Allow an empty answer if the paper doesn’t say** permits a present detail to have no known value. This is different from leaving the detail out:

| Inclusion rule | Empty answer allowed | When the paper gives no answer |
|----------------|----------------------|--------------------------------|
| Required | No | The missing value fails structure validation; do not invent an answer. |
| Required | Yes | The detail must appear, but may have an empty value (`null`). |
| Optional | No | The detail may be omitted. |
| Optional | Yes | The detail may be omitted or included with an empty value. |

The editor manages internal field identifiers for you. Renaming a detail keeps its identity; it does not create a second output field.

AI Chat proposals appear in the shared review dialog. **Cancel** leaves the draft unchanged; **Apply** changes only the unsaved draft. A stale proposal must be regenerated. You can undo the last accepted change while the draft still matches it. Only your separate **Save** creates a saved revision.

## Understand validation and revisions

Every custom profile enforces its declared structure. Semantic validation is separate and optional: it requires an explicitly attached, compatible validator. A field without a mapping is structurally checked, not semantically verified. Do not assume reagent names or stock identifiers have a validator just because the field has a familiar name.

Saved agents and flow nodes retain exact revision identities. Editing an agent or profile later does not silently update an existing flow node. Select the new revision explicitly and reverify the flow when you want it to change. Review the saved output and validation findings before exporting.

## Attach a validator to a detail or part

Open the detail and choose **Validation → Add a validator**. Search the available
built-in and custom validators in **Search built-in and custom validators**.
Select how to use the detail under **Use “[detail name]” as**, then choose
**Attach validator**. Use **Load more validators** if the current results do not
include the one you need. A text answer is not
automatically gene or reference information: choose a validator that matches its meaning.

Use **Edit validator settings** for additional inputs, fixed values, result destinations,
and unresolved-answer policies. These settings change the Workshop draft; Workshop Save
creates the saved revision. Fields requiring additional inputs must have those configured
before the profile can be saved. **All validator settings** also retains mappings for
removed or incompatible fields so you can repair or remove them explicitly.

The details table, parts table and review show **Validator attached**. The parent answer
separately reports how many parts have validators. “Yes” means an association is configured,
not that an extracted answer passed validation. Structural validation always applies.

You can also ask **AI Chat** to attach, change, or remove a validator. For example:
“Attach my gene validator to the Gene identifier part of Gene details, and keep the
other details unchanged.” The assistant uses the same available validator catalog and
can explain the input association. Review its proposal and choose **Apply** to update
the live draft and attachment indicators. **Save** remains a separate step. Asking for
an attachment does not run validation on a paper or mean its answers are validated.

The picker includes custom validators when they are based on a supported packaged validator and keep its expected result format. Naming an arbitrary agent “validator” does not make it eligible. You also need access to the saved validator and its tools when it runs.

An attachment keeps the custom validator's saved revision. Editing that validator's prompt later does not change an existing attachment. Review and update the attachment when you want the new version. There is currently no supported stock, supplier, or catalog-number validator.

## Validate an allele

Choose **Allele validation** for a text detail containing an allele identifier,
symbol or short description. This also works for an individual part of an answer.
Map that detail to **Mention**. Species (**Taxon**) and an associated gene can be
supplied from another detail or as a fixed value when known. Paper evidence is
optional context for this lookup; you do not need to add a quote field.

The validator searches Alliance allele records. An identifier may identify a
single record directly. A description such as “Ccr2 knockout” can match several
alleles, so it remains unresolved unless the available information distinguishes
one. Unresolved results require curator review and block readiness/export under
this validator's policy. Attaching it is optional.

A confirmed identity does not establish that the allele should be curated from
the paper or that a custom record is ready for Alliance submission. Extraction
still retains paper evidence. The allele submission envelope remains in development.
Saved custom validators based on the allele validator can also be selected when
they retain its supported result contract and you have access to them.
