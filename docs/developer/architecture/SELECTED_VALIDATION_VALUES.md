# Selected validation values

Validation completion and scientific acceptance are separate facts. The runtime
retains each normalized proposal with its exact immutable completion; it does not
write the proposal into the annotation. `validation_history.selections` declares
review obligations before selectors and execution, including work that cannot run.
An unresolved but structurally complete answer can be selected. An incomplete
answer can receive an opinion, but cannot satisfy publication readiness.

`publish_decisions` is the shared transaction boundary. It applies version-checked
decisions, rebuilds from immutable extraction/curator targets and selected result
recipes, writes a content-addressed projection and selection manifest, checkpoints
the envelope, and refreshes existing workspace draft projections. A failure rolls
all of these writes back. The lock order is artifact before envelope. Package
mappings and profile whole-record checks still determine structural validity.
Composite identities cannot be assembled from different answers. Validator-created
objects name their exact contributing completion and receive immutable targets in
that same transaction.

A new review episode does not erase the prior selected value. Current inspection
may show that value with pending episode readiness. Final export requires an exact
publication for the current episode. Reject records a review decision; revoke is
the explicit operation that removes a selection. Retired targets never revive
because a delayed answer arrives. Every historical publication remains readable
within its original owner/document/session scope.

Curator clear deliberately blanks identity. `selected_value_action=withdraw_override`
exposes only a currently eligible selection, otherwise a blank identity.
`restore_selected` requires `selection_reference` naming an exact historically
ready publication of the same artifact. These operations use the existing curator
permissions, before-value and revision checks. Profile actions address the whole
identity container. Typing an old value no longer automatically restores an old
validation snapshot.

`inspect_results(result_view="accepted")`, formatter projection, and submission use
the shared publication reader. Original and post-validation inspection remain
separate historical views. A normal workspace readiness check consumes the selected
publication instead of launching another validation episode; explicit forced
validation can start reconsideration. Existing required-field rules and curator
submission/waiver permissions remain in effect. Historical data without explicit
review is inspectable but is not silently approved for a new finalized export.

Exports pin selection references in metadata and formatter receipts. Different
content, selected manifests, format, descriptor or flow branch produces a different
file identity. Exact repeats are idempotent. Older files retain their content.
TraceReview exposes selected publication references, episode IDs and pending-answer
markers. Pending review is a product state, not an operational Sentry failure.

The next umbrella tickets provide supervisor decision tools and orchestration.
Dependency waves pause pending review; their resume caller must use
`selected_prerequisites` and `ValidatorDispatchJob.history_prerequisites`, which
are rechecked when the attempt starts and when a selected result is read. A prior
completion ID by itself is never an accepted prerequisite. No review-off fallback
or deployment switch is supplied by this foundational change.
