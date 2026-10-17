# Required document read failures

A missing required chunk, failed source retrieval, or invalid exact-text/span
result stops the current extraction. It must not become an empty extraction,
recoverable validator finding, or instruction to continue without source text.
An empty search or unmatched section query remains a valid discovery result.
Structured cursor and response-size errors retain their existing correction and
reporting paths; they do not assert that PDF source retrieval failed.

Package document tools raise `DocumentReadError` with the original exception
chained as its cause. The SDK failure callback raises instead of turning a
retrieval exception into a string. The document adapter validates success shape
before model projection, including requests for full canonical results. It
never parses SDK error prose or accepts strings as successful document results.

Supervisor tool wrappers and validator dispatch preserve this terminal failure.
The existing flow outcome owner stops downstream execution and prevents final
output from being reported as successful. Batch processing fails the affected
document while retaining its existing scheduling policy for other documents.
Existing canonical evidence is retained for diagnosis, not reported as a
completed extraction. Already-running synchronous operations cannot be forcibly
stopped; queued validator operations are cancelled and admitted work is drained.

Chat, flow and batch use the shared safe message: “There was an error reading
the PDF. We couldn't continue the extraction.” This does not assert corrupt PDF
bytes. Source text, identifiers and raw exception messages are not included.
Existing terminal reporting facades retain chained causes and sanitized trace
correlation; this contract adds no parallel Sentry capture or new event type.
TraceReview continues to consume the existing RUN_ERROR/FLOW_ERROR events and
canonical tool records.
