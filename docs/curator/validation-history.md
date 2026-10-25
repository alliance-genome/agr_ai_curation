# Validation history and another review round

Open a record's existing **Details** panel and expand **Validation history**. It shows the original extraction, saved validator answers, review decisions, curator edits, and any targeted extractor follow-up. Exact saved details load on request and can be read in full through continuation pages. Opening history never runs an agent.

**Revalidate record** requests another bounded review of that record. Add optional instructions explaining what needs another look. The validator receives the relevant original extraction, earlier answers and decisions, and your instructions. The configured supervisor reviews its answer. Other records keep their saved values and reviews; required dependencies are reused when already accepted.

A deliberate request authorizes an accepted answer to replace the value that existed when you requested the round. Edits made after that request remain protected. Your manual decisions remain available even if the automatic review reaches its allowance. You can explicitly request another round; reopening, resuming, or retrying the same HTTP request does not grant another allowance.

**Revalidate all records** explicitly selects the workspace's records. Operations are saved before execution, and reopening displays their saved progress. Stop cancels remaining work. Interrupted submissions with an unknown outcome are retained for inspection and are not automatically replayed.

Accepting or rejecting a saved validator answer records your reason and updates the selected value through the shared history. Rejecting a replacement preserves an earlier accepted selection. These scientific decisions are separate from accepting or rejecting an annotation row for submission. Finishing one record's review does not claim that every record is ready to submit.
