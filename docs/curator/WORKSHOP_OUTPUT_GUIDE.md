# Ask Workshop about results and outputs

Use AI Chat in Agent Studio to explain an agent, review a saved flow, or prepare
changes. Ask in your own curation vocabulary. You do not need to know tool names
or internal field keys. Review each proposal: **Apply** updates the draft;
**Save** keeps it in your account. Neither action means the flow has run.

## Paper wording and checked values

Extraction records what the paper says and preserves supporting evidence.
Validators check supported identities against the relevant database or ontology.
A name or identifier printed in the paper is not automatically a confirmed match.
Species must be supported by the paper; your selected group does not establish it.

In CSV, TSV and chat, a resolved value normally appears as **label (ID)**. A value
that has not been resolved appears as **UNRESOLVED**. Ask for paper wording in a
separate column if you need to compare it with the checked answer. The application
does not fill a missing checked value with a different field. An unresolved marker
on one part of a compound answer does not mean every part failed.

Useful requests:

- “Show the paper's wording beside the resolved term and its validation result.”
- “Explain why this term is unresolved, using this run's evidence.”
- “Show the stored reason this item was selected.”

The **Rationale** is the extractor's stored explanation, not a new explanation
written by the output agent or proof that validation passed. Older results may not
have a recorded rationale. Review the evidence separately. A curator validation
override records your deliberate decision; it is not a fresh database confirmation.

## One cell or several columns

A list can remain in one readable CSV/TSV/chat cell, or its items can appear in
separate columns. JSON preserves the list and its structure.

- “Keep every phenotype term in one cell.”
- “Put the terms in columns named Term 1, Term 2, and so on.”
- “Name the first two columns First term and Second term.”

Named split columns represent positions in the list. Naming them “Maternal” and
“Paternal” would not make the application sort the biology into those categories.
A single value occupies one column; splitting does not break ordinary text at
commas. Separate parts of an answer, such as supplier name and stock number, can
also be selected as distinct fields.

All items must fit: if a result has more terms than the supplied names or the
server's column limit, the output reports an error rather than silently dropping
terms. Ask for additional names, numbered names, or the whole list in one cell.
Workshop checks the configuration before proposing it; the actual longest list
can only be checked once results exist.

## Layout and execution are separate choices

**Use selected fields** keeps a saved layout. **Let AI arrange the output** lets
instructions guide a supported layout. In either case, outputs use saved results,
not model-written substitutes for the data. Conditional columns such as “use the
resolved term, otherwise the paper wording” are not supported; use two columns.

For file outputs, direct structured export skips the formatter AI and copies the
selected saved values. Formatter prompts do not run in this mode. Choose AI output
when you need its supported instruction-guided arrangement. Selecting fields does
not by itself authorize switching modes, and changing an agent's default does not
change existing flow steps. Ask Workshop to explain the proposed choice.

An output branch attaches to its extraction source. Multiple branches can produce
different files. Records from different sources stay separate; they are not
automatically joined into combined gene–allele–phenotype rows.

## Check what validation is available

Ask “Which checks will actually run, and what remains unchecked?” An attached
validator is configuration, not an already successful result. Some declared checks
remain under development and do not run automatically. Downloadable output and
database-submission readiness are different things.

- Phenotype assertions can retain several ontology terms. Select the full term
  list, not just the primary display label. Term validation is active; subject
  and reference resolution remain under development.
- Disease subjects can be genes, alleles or affected genomic models. Their allowed
  relationships and subject checks differ. A model line is not interchangeable
  with a gene merely because both appear in the paper.
- Gene-expression extraction includes measured expression changes after a
  perturbation, but not rescue-only overexpression or marker-only cell labeling.
  Reagent context can be retained without reagent, specimen or allele validation.
- Custom structures collect the details you define. Only compatible, explicitly
  attached validators check those details; a custom structure is not automatically
  a submission format.

## Troubleshoot without losing your work

Give Workshop the affected paper, flow or Run ID and describe what you expected.
It can inspect saved results and available run evidence without repeating the
extraction. A shortened preview does not mean the full answer or evidence is lost.
If inspection is incomplete, the assistant should identify what remains unchecked,
not call the flow verified.

After an extractor's structure changes, an output step may need its fields
reconfirmed: open the output step, choose **Choose output fields**, review the
selection, and save. Not every error has this cause; inspect the reported error
first. **New chat** does not repair an incompatible saved layout.

Saved flows keep exact agent revisions. Updating an agent alone does not replace
the revision used by a flow. For an unavailable model or unsupported setting, ask
Workshop to inspect the saved revision and offer current supported options; review
the corresponding flow update too. Existing custom instructions should be preserved.

Tools can be loaded on demand, so a tool not listed in an initial view is not
necessarily unavailable. You do not need to manage tool-search groups. If a run
hits a server limit, ask for the specific limit and a supported next step. A cost
that is missing or shown as zero without pricing information is not proof of a
free run.
