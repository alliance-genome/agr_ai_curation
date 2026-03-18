# Tools Directory

This repo directory is no longer the public customization path for standalone
installs. Public or organization-specific tools should be packaged under
`~/.agr_ai_curation/runtime/packages/<package>/` and declared through that
package's `tools/bindings.yaml`.

See [Modular Packages and Upgrades](../../docs/deployment/modular-packages.md)
for the installed runtime layout and
[config/agents/README.md](../../config/agents/README.md) for how agents consume
those tool IDs.

## Package-first layout

```text
~/.agr_ai_curation/runtime/packages/org_custom/
├── package.yaml
├── requirements/runtime.txt
├── python/
│   └── src/
│       └── org_custom/
│           └── tools/
│               └── my_tool.py
└── tools/
    └── bindings.yaml
```

Minimal binding declaration:

```yaml
package_id: org.custom
bindings_api_version: 1.0.0
tools:
  - tool_id: my_tool
    binding_kind: static
    callable: org_custom.tools.my_tool:my_tool
    required_context: []
    description: Query our internal service for data
    source_file: python/src/org_custom/tools/my_tool.py
```

`source_file` is optional provenance metadata. Include it when you want
diagnostics or admin tooling to point back to the package-relative source path;
omit it if you do not need that breadcrumb.

## Add a package-owned tool

1. Create a Python module inside your package's `python/src/.../tools/`
   directory.
2. Implement the callable with `@function_tool`.
3. Declare the exported tool ID in `tools/bindings.yaml`.
4. Install or copy the package directory into
   `~/.agr_ai_curation/runtime/packages/`.
5. Reference the tool ID from a package-owned agent bundle.

Example implementation:

```python
from agents import function_tool


@function_tool(
    name_override="my_tool",
    description_override="Query our internal service for data",
)
async def my_tool(query: str, limit: int = 10) -> dict:
    """Return clean, structured data for agent consumption."""
    return {"results": [], "total": 0}
```

## Loading and override behavior

At runtime, the backend:

1. Discovers packages from `runtime/packages/`.
2. Loads each package's `tools/bindings.yaml`.
3. Builds a merged tool registry keyed by `tool_id`.
4. Creates an isolated virtual environment per package under
   `runtime/state/package_runner/<package_id>/venv` when package tools execute.

Important rules:

- Unique tool IDs load normally.
- Conflicting tool IDs do not get an implicit winner; operators must pick one in
  `runtime/config/overrides.yaml`.
- Whole packages can also be disabled in `runtime/config/overrides.yaml`.

## Repo-local use in this checkout

Use repository paths only when you are maintaining the shipped Alliance package
tool catalog or
working on runtime internals from source. For that work:

- keep shipped tool callables in `packages/alliance/python/src/agr_ai_curation_alliance/tools/`,
- keep shipped bindings in `packages/alliance/tools/bindings.yaml`, and
- treat `backend/tools/` as reference material rather than the public contract.

## Key principle

Tools should handle their own data transformation and return clean, structured
data so agents do not need to post-process raw service responses.
