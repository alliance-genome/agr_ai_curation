# Extraction validation notices

Flow Builder shows a non-blocking notice for each extraction step with zero
connected database validator bindings. It does not require acknowledgment on
save, agent editing, execution, batch launch, or API requests.

## Scope

- Automatic active bindings and custom output structure mappings count for their
  own extraction step. Structural checks without a validator binding do not.
- Custom validator edges count only when connected directly to the extraction
  step with a validation attachment role and binding. Ordinary control-flow
  edges and unrelated validator steps do not count.
- A configured but unavailable binding is still configured. Existing errors
  require repair; the missing-validation notice is not an error bypass.
- One connected validator suppresses the notice. There are no partial-coverage
  warnings and no claim that every field is verified. Unsupported data such as
  stock-provider names does not acquire a guessed validation requirement.
- Formatter, validator, chat and other non-extraction steps are excluded.

## Interaction and persistence

The amber notice sits above the canvas at bottom-left, beside the zoom controls.
It names the step and offers inline help and Dismiss without taking focus.
Dismissal is local to the browser, signed-in user, flow, node and output/check
configuration. It survives reloads and transfers when a draft is first saved.
New drafts have independent identities retained with browser draft recovery.
Changing the extractor, output contract or validation connections, or deleting
and recreating a node, invalidates the old dismissal. Prompt-only revisions,
renaming and moving nodes do not. Browser storage failure never blocks editing.

Dismissal is a display preference, not consent or database verification. The
feature does not modify run results, exports, readiness or submission rules.
Required packaged validation and existing availability/access checks remain.

The earlier unshipped ALL-1241 acknowledgment API, table migration, dialogs and
runtime enforcement were removed when this design replaced them. Production
never received that migration; no production data cleanup is required.
