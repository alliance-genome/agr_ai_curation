# Workspace validation review

Workspace review commands are persisted in `workspace_review_operations`. A caller-generated request UUID is scoped to the authenticated owner; duplicate delivery with changed arguments is rejected. Empty candidate IDs explicitly mean all records in that workspace. Capture curator replacement authority at request time, not worker startup.

Workers claim a short database lease and serialize operations sharing an artifact. Preparation is recoverable through deterministic episode and saved-plan identities. The provider work runs outside SQL transactions. Expired running operations become uncertain and cannot be automatically replayed. Resume retains the same scope and usage; a deliberate new curator command grants a linked, separate bounded scope. Immutable request identity and prior history cannot be deleted or rewritten.

A scoped episode retains untouched coverage by reference to actual earlier reviews. `review_target_ids` covers the requested record and configured dependencies; `review_ready` is distinct from whole-artifact `ready`. The shared adapter limits initial jobs, dependent waves, reconsideration, source follow-up, and decisions to that scope. Accepted dependencies can be reused without another provider call. Scientific judgments remain with agents and curators, not programmatic status heuristics.

The former synchronous candidate `/validate` and session `/validate-all` routes are retired. Submission and edits use structural/selected-state refresh only. New commands use `/sessions/{id}/review-operations`; status, cancel, and resume use `/review-operations/{id}`. History and exact body continuations are authenticated record reads. Current record/session operation reads restore progress without creating work.

The backend lifespan runs the durable queue. `WORKSPACE_REVIEW_CONCURRENCY` controls workers per process (default 1); replicas multiply that concurrency. Existing validation-review environment settings govern bounded rounds. `VALIDATION_HISTORY_PAGE_SIZE` controls history pages. Both development and production Compose forward these settings.

Each execution has a `workspace-validation-review` Langfuse root and the existing durable `validation_review.*` event stream. TraceReview extracts workspace requests alongside shared validation activity. Unexpected handled failures use the sanitized shared reporting facade; ordinary scientific uncertainty, limits, cancellation, and access conflicts are not Sentry failures. Local fake-provider/persistence tests do not verify production ingestion.
