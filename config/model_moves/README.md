# Model moves

A model move retires one or more models from the catalog (`config/models.yaml` and
`packages/core/config/models.yaml`, which must stay identical) and moves everything
saved on them to their replacements, so no curator has to re-save anything.

Each move is a small reviewed file here, named `<date>-<target>.yaml`. The newest
file is the one the persistence tests check against the catalog.

```yaml
moves:
  - from: [gpt-old-sol]        # retired ids; none may still be in the catalog
    to: gpt-new-sol            # must be in the catalog
    reasoning: {}              # saved level -> new level; {} keeps every level
    note: >-                   # stored on every appended version (curators read it)
      vX.Y.Z release: ...
```

## What the command changes

`python -m src.services.model_move_cli` (in the backend image) runs every move in one
pass and one transaction:

- Every custom agent (archived ones included) whose saved head names a retired model,
  or which has an older version pinned by an active flow step on a retired model, gets
  a copy of each such version with only the model (and the reviewed reasoning mapping)
  changed. The head copy stays the head. Saved versions are never edited.
- Each such flow step is re-pinned to the copy of the version it pinned, and the flow
  is saved through the normal flow save as its owner, with the owner's groups. A check
  refuses any change beyond the pins. A flow pinning versions on two retired models is
  re-pinned in one save.
- The editable row of every non-system agent still on a retired model (an agent whose
  head is current, or that has no saved version) moves to the target.
- System agents are not touched: startup rewrites them from package YAML and the
  environment. Historical records (cost ledger, benchmark runs and imports) keep the
  model they ran on. In-flight validation-review work pins its model: drain it first.

Anything that cannot be converted (for example a saved reasoning level the target does
not offer and the file does not map) is left wholly unchanged and reported; the command
exits 3. A re-run converts only what is left and appends nothing for what is done.

## Order

1. The catalog no longer offers the retired ids and offers the targets (the release).
2. Every model setting in the deployment environment names a catalog model (the
   command refuses otherwise, as startup does).
3. `--list-flow-owners`, then build the owners' identity-provider groups file
   `{"<user_id>": ["<provider group>", ...]}`. The command maps them to active groups
   the way a login does.
4. Dry run (`--provider-groups FILE`), review refusals, then `--apply`, then a re-run
   that must append nothing.
5. `--verify` must report `"blocking": 0` before the backend starts. Startup refuses an
   active agent row on a model the catalog does not offer.

```bash
python -m src.services.model_move_cli --moves /runtime/config/model_moves/<file> --list-flow-owners
python -m src.services.model_move_cli --moves /runtime/config/model_moves/<file> --provider-groups /work/groups.json
python -m src.services.model_move_cli --moves /runtime/config/model_moves/<file> --provider-groups /work/groups.json --apply
python -m src.services.model_move_cli --moves /runtime/config/model_moves/<file> --verify
```

Exit codes: 0 done (or nothing left), 1 refused to start (nothing written), 3 something
refused and left unchanged (or `--verify` found something left). The JSON report names
agents and flows; keep it with the private deployment notes.

There is no targeted undo: the old versions name a model the catalog no longer offers.
Recovery from a bad apply is the pre-window database restore.
