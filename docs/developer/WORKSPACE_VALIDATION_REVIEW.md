# Workspace validation review

Workspace review commands are persisted in `workspace_review_operations`. A caller-generated request UUID is scoped to the authenticated owner; duplicate delivery with changed arguments is rejected. Empty candidate IDs explicitly mean all records in that workspace. Capture curator replacement authority at request time, not worker startup.

Workers claim a short database lease and serialize operations sharing an artifact. Preparation is recoverable through deterministic episode and saved-plan identities. The provider work runs outside SQL transactions. Expired running operations become uncertain and cannot be automatically replayed. Resume retains the same scope and usage; a deliberate new curator command grants a linked, separate bounded scope. Immutable request identity and prior history cannot be deleted or rewritten.

A scoped episode retains untouched coverage by reference to actual earlier reviews. `review_target_ids` covers the requested record and configured dependencies; `review_ready` is distinct from whole-artifact `ready`. The shared adapter limits initial jobs, dependent waves, reconsideration, source follow-up, and decisions to that scope. Accepted dependencies can be reused without another provider call. Scientific judgments remain with agents and curators, not programmatic status heuristics.

The former synchronous candidate `/validate` and session `/validate-all` routes are retired. Submission and edits use structural/selected-state refresh only. New commands use `/sessions/{id}/review-operations`; status, cancel, and resume use `/review-operations/{id}`. History and exact body continuations are authenticated record reads. Current record/session operation reads restore progress without creating work.

The backend lifespan runs the durable queue. `WORKSPACE_REVIEW_CONCURRENCY` controls workers per process (default 1); replicas multiply that concurrency. Existing validation-review environment settings govern bounded rounds. `VALIDATION_HISTORY_PAGE_SIZE` controls history pages. Both development and production Compose forward these settings.

Each execution has a `workspace-validation-review` Langfuse root and the existing durable `validation_review.*` event stream. TraceReview extracts workspace requests alongside shared validation activity. Unexpected handled failures use the sanitized shared reporting facade; ordinary scientific uncertainty, limits, cancellation, and access conflicts are not Sentry failures. Local fake-provider/persistence tests do not verify production ingestion.

### Current authorization at queue pickup

A saved command is not a permanent access grant. Before preparation/recovery and
before each artifact starts paid work, the worker resolves the owner's current
principal, checks the active local account, and requires all saved groups to
remain granted. Newly added groups never widen the saved execution context.
Denied access stops for human review; unavailable authorization fails explicitly
and uses the sanitized operational reporting facade. Neither case calls a model.

The configured provider must supply `agr_ai_curation.current_subject_resolvers`
(or use the built-in Cognito adapter). It receives the trusted local subject and
returns an authoritative enabled `AuthPrincipal`. No bearer token is persisted.
Cognito requires `cognito-idp:ListUsers`, `cognito-idp:AdminGetUser`, and
`cognito-idp:AdminListGroupsForUser` on the configured pool. Exact-sub discovery
only locates a username; authoritative account and complete membership checks
still follow. Development auth has no authoritative resolver and therefore
cannot execute queued model work; tests inject a fake trusted resolver.

All-records commands group eligible records by artifact. Records without a durable
validation source are explicitly listed as excluded in the saved outcome; a
single-record request for such a record fails rather than pretending validation
ran. Command status and cancellation authorize the saved artifact scope, so
removing a workspace row does not erase or strand its operation. Admission is
serialized with cancellation and episode references are saved before grants.
The session's all-records status uses exact empty candidate-list matching;
single-record and bootstrap rounds cannot be mistaken for that command.

The workspace page observes active commands even after Details closes. Ambiguous
HTTP submissions retain their request identity in tab-local session storage until
acknowledged. Reload/reopen retry therefore does not grant another cycle.
