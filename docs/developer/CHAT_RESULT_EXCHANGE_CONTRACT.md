# Chat result exchange and recovery contract

This describes the October 2026 chat hardening changes. It is an implementation
contract, not evidence that a particular release has reached production.

## The result is distinct from its preview

An extractor stages evidence-backed candidates and explicitly finalizes the IDs
it retains. All registered builder finalizers accept `candidate_ids=[]` as an
intentional empty result. A final explanation alone is not a finalized result.
The shared root and nested runners add this completion instruction at runtime,
including for older immutable custom-agent prompt snapshots. They preserve the
curator's scientific instructions and do not invent an object to avoid zero.

After finalization, applicable validators run. Both packaged materialization and
custom-profile materialization save each complete typed validator result before
selecting fields for findings. The saved envelope has a `validator_results`
mapping; each entry contains the full result, full request, source envelope
revision and append order. Findings reference that entry. A content-addressed
identity deduplicates the same result/request/revision; a later revision or
changed result retains a separate original. Existing finding projections remain
compact views. They are not the only copy of a new result.

“Original” means the accepted typed runtime JSON, including nulls and nested
lookup/candidate/explanation data. It does not mean the unvalidated provider
response, the complete tool transcript, or every failed intermediate attempt.
Tool transcripts remain in observability records. Historical finding projections
are never promoted into fabricated originals. Reads explicitly report when an
original was not retained by an older release.

The supervisor receives the extractor's complete authored explanation plus a
structured manifest. The explanation is not silently replaced by a generated
summary. The manifest uses saved objects and findings for persisted state; prose
does not override those records. This is one important context tradeoff: the
structured preview is bounded, while the authored explanation remains complete
and can be large. Existing request measurements expose its cost.

## Counts and multiple validation rounds

The manifest reports separate units:

- extraction objects;
- validator decisions, with actual status counts;
- current and historical decisions;
- findings, including open error and warning counts;
- incomplete outputs and originals unavailable in historical data.

A decision is associated with its binding and structured target. Source revision
and recorded order distinguish successive decisions for the same target. An
older rejection followed by a resolved alternative is not, by itself, an
unresolved final object. Closed historical rejections remain inspectable. Open
findings remain outstanding until the application resolves or waives them.
No code generates semantic narratives such as “A conflicted with B, therefore
unresolved.” Scientific resolution remains in the extractor, validator and
curator records.

Preview ordering prioritizes outstanding rejected writebacks, open errors, other
outstanding decisions, then current decisions and stored history. Coverage gives
the number returned, outstanding returned, outstanding remaining and whether
all detail is present. Stable complete decision keys and exact read requests stay
with each preview. Large resolved values may be omitted from the preview with
`details_omitted`; the saved original is unchanged. Open findings without a
validator decision remain counted and accessible through the findings view.

## Exact expansion through one inspection tool

The supervisor retains one `inspect_results` tool. This change adds actions and
result references, not each specialist's tool catalog to the supervisor.

An extraction reference supports summaries, objects, evidence, findings,
validator decisions and exact detail paths. `validator_results` returns stable
keys; a selected key can read `original.result` or `original.request`. Existing
byte-bounded pages, exact text windows, hashes and continuation checks handle
large values. A stale continuation must be restarted against the current result.

Standalone validators do not create artificial extraction objects. Their full
results are committed to `validator_result_records` before a handoff advertises
a `validator-result:<uuid>` reference. `validator_runs` lists this chat's saved
validator invocations. The same inspection tool reads a selected original.
Every storage read checks the authenticated owner. Normal reads also check the
current session; explicit `all_authorized` reads still require the same owner.
Reusing an invocation ID with different content fails instead of overwriting
an original. A failed persistence operation prevents a successful handoff.

## Evidence follows the statement

The shared output projection joins each object's evidence IDs to complete saved
evidence records within that object's source artifact. The fields are
`object.supporting_evidence` and `object.evidence_link_issues`. Multiple quotes
stay with their statement. Identical records may be deduplicated; conflicting
records with the same ID are reported as ambiguous. Missing evidence is reported
as missing. Labels, row order and another artifact are never used as guesses.

Chat and file formatters receive runtime guidance to select these fields when
supporting evidence is requested. Final rendering includes every selected row;
formatter tool previews remain bounded. Explicit saved selected-fields layouts
retain their columns. Adding available fields changes the source-catalog
fingerprint, so current saved layouts require a guarded additive fingerprint
migration. Already-stale layouts must not be silently certified as current.

## Context, compaction and measurements

There is no new global context cap or semantic summarizer. Existing request
instrumentation measures instructions, inputs, tools and output schemas for SDK
and direct-client calls. Output reserve is recorded separately when configured;
null means unknown, not zero. Provider-reported input/output/cached-token usage
is actual usage. Locally computed sizes and token estimates remain estimates.
Provider-held context is explicitly unknown when it cannot be inspected.

Standard automatic chat uses the existing OpenAI Responses compaction session.
The wrapper now records before/after item counts, visible bytes, estimated tokens,
pending tool pairs, provider-held-context presence and whether the SDK deferred,
replaced or left the projection unchanged. It does not change the SDK algorithm
or claim that numerical reduction proves semantic fidelity. Canonical chat history
and durable extraction/validator originals remain separate from the compacted
model context. Exact prior user/assistant text is available through the existing
history-recall tool.

The configured standard-chat threshold (currently 70% of a configured 400,000
context budget) applies only where that session is installed. Direct specialist,
nested validator, formatter, flow, Studio and benchmark calls must not be
advertised as receiving automatic compaction from this wrapper. Their request
measurement is shared, but their context construction differs. The current
operational values remain environment-configurable; this release adds no new
numeric operational limits. Calibration against future real traffic can revise
those settings without pretending tonight's offline evidence measures model
quality or provider latency.

## Curator-visible outcomes and retry

Typed provider overload, connection and rate-limit failures produce understandable
retry-later notices. Context limits suggest reducing scope or starting a new
chat. Invalid finalized results, quota/authentication problems and persistence
failures have different guidance; they are not all labeled overload. Raw provider
exception payloads remain diagnostic data, not the main chat error text.

Failed and interrupted ordinary chat turns retain the original request and a
safe notice in history, even when no extraction exists. Available partial text
is labeled as unfinished. Already persisted extraction results remain available
for review. Generated flow files are released only after their failure/partial
transcript is committed. Provisional success text is withheld from failed flows.
A partial flow remains visibly partial after replay or history reload.

`Restore request` copies the original request into an empty composer. It does not
send it, repeat tools, overwrite a draft or act during a running request. The
curator can review the current document and flow before sending a new turn. A
repeated turn ID replays the durable outcome, including failure, rather than
silently rerunning work. Automatic assistant-save rescue is reserved for completed
responses; a failed notice that could not be saved explicitly disables that rescue
and tells the curator to copy visible text before leaving.

## Coverage and operational verification

Packaged and profile-bound validators share original retention. Root direct and
nested specialist runners share durable standalone result storage and builder
completion guidance. Automatic chat and saved flows use the shared manifest,
inspection and projection code. Batch flows use the same extractor/validator/
formatter execution paths and retain their separate document/ZIP outcome model.
Agent Studio authoring chat retains its own explicit refusal, incomplete, turn
limit and provider-error UI; its execution tools reach the same runtime services.
Benchmarks use the same model measurement and typed execution paths, retain
experiment failures, and do not acquire a normal-chat compaction session.

Release evidence must include all saved heads, pinned revisions, inactive flows,
profiles, prompt templates and system agents. Read-only inventory and a restored
production copy are required to detect stale selected-field catalogs, overrides
and prompt pins. Do not rewrite old scientific results to match this contract.
Private release records contain exact counts, affected IDs, mechanical migration
plans, checksums and test logs; this public document intentionally contains no
curator data or production identifiers.
