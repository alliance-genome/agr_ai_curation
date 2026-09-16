# Returned tool outcomes

Classification is shallow and producer-scoped. Tool completion means a response
arrived, not necessarily that the operation succeeded. Alert eligibility is a
separate decision. Unknown shapes and free-form text retain prior behavior.

## Supported contract inventory (ALL-1223)

| Producer | Contract | Completion | Alert owner |
| --- | --- | --- | --- |
| Studio registered tools/proposals | top-level boolean `success` or `status=error` | explicit failure is unsuccessful | only typed operational authoring codes below |
| Flow proposal compiler | `failure_kind=operational`, `code=flow_authoring_compile_failed` | unsuccessful | returned-result capture |
| Canonical authoring engine | `code=authoring_validation_engine_failure` plus backend failure ID/capture outcome | unsuccessful | existing engine reporter; Studio adopts its receipt for explicit-report reuse |
| Code inspection | `status=error`, `invalid_regex`/`invalid_match_mode` | unsuccessful, repairable | no operational alert |
| AGR query and extraction builder results | `status=error`, `failure_classification=validation_failed`/`blocked` | unsuccessful, repairable | no operational alert |
| AGR query lookup | `lookup_status=transient` | unsuccessful | returned-result capture |
| AGR query empty lookup | successful result or `lookup_status=not_found` | preserve producer's explicit status | no operational alert |
| Identifier import item | `status=error`, `document_source_access_denied` | unsuccessful | no operational alert |
| Identifier import caught dependency/config/download exception | `document_source_unavailable` | unsuccessful | import service captures original exception once, preserves per-item/batch results |
| Unknown dictionaries, nested result data, arbitrary error-looking strings | no recognized producer contract | unchanged | no inferred alert |

No recursive scan or error-string heuristic is used. SDK plain-text tool errors
remain unmapped; terminal/exhausted failures retain their existing owner.
Identifier import batches are handled at the producer, not by guessing from
nested item dictionaries. User Stop remains owned by cancellation handling.

Automatic capture, explicit reporting and terminal propagation must only share
ownership when tied to the same backend-generated failure identity or exception
cause. Tool name, error wording or a shared session alone is not identity.
Independent later failures must remain reportable. No process-global dedup cache.

Terminal suppression requires an explicit exception cause linked to an already
captured failure. A later missing-finalization or unrelated runtime exception is
not inferred to be the same incident merely because an earlier tool failed.
The AGR query error log remains diagnostic but is excluded from promoted Sentry
events because the structured transient result owns tool-level capture.

Queued capture is not evidence of ingestion or email delivery. ALL-529 verifies
delivery separately. No SNS fallback is introduced.
