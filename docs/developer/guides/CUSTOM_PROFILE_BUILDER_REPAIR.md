# Repairing custom-output builder configurations

A saved agent using `profile_bound_generic` must use the installed generic
builder lifecycle. Scientific prompt edits cannot repair an incompatible saved
tool list. Runtime construction continues to reject incompatible revisions.

## Reviewed repair through Workshop

1. Inspect the current saved revision, output profile, tool list and flow pins.
   Keep a before/after comparison of the curator's scientific instructions,
   group rules, item/detail guidance, evidence/document tools, model/reasoning,
   visibility and sharing settings.
2. Explain the mechanical change to the curator before saving: retaining Custom
   Output Structure replaces package-declared domain builders with generic
   stage, patch, discard, list, find and finalization tools. It does not change
   their collected fields or add identity validators. Do not manually rename
   tools or rewrite scientific instructions to simulate this transition.
3. With curator approval, open the agent in Workshop, retain its intended Custom
   Output Structure and save. Save creates a new execution revision and chooses
   the matching installed builders. All other capabilities are retained. A
   concurrent revision conflict must be resolved by reloading, not overwriting.
4. Compare the new revision against the recorded settings. Construct the exact
   saved revision in an approved test environment and verify its output contract,
   actual records and evidence on a representative paper.
5. Only after review, propose updating the existing flow step to the new revision.
   Apply changes updates a draft; saving or executing the flow is a separate
   curator decision. Existing flow pins and historical execution revisions are
   never rewritten by the agent save.

Later saves and clones retain the installed lifecycle tools as system-managed
capabilities, subject to current execution policy. Explicitly changing a generic
extractor to No Structured Output removes its generic lifecycle tools while
retaining unrelated capabilities; it does not weaken the no-output runtime guard.

Package tool bindings declare lifecycle membership with `builder_output_mode`:
`domain` or `generic`. Evidence tools and controlled-field resolvers are not
builder lifecycle replacements. Unknown finalizers without this metadata must
be reviewed before a format transition. Missing installed generic builders block
the save; execution must not silently grant tools as a fallback.

## Chat preparation scope

Chat preparation previews exact, owned saved-result references and their
materialized candidate count. Confirmation requires a later unqualified user
affirmative immediately following that preview turn. Approval is scoped to the
user, session, document and result contents, expires according to
`CHAT_CURATION_CONFIRMATION_TTL_SECONDS`, and can be consumed once across workers.
Changed scope requires another preview. Candidate subsets within an extraction
are currently unsupported and must fail without preparing the entire result.

This repair path requires deployment and test acceptance before use in production.
