# Custom Tools

This folder is kept only for repository-local reference and legacy experiments.
Standard installs should not add organization-specific tools here.

For public or deployment-specific customization, create a runtime package under
`~/.agr_ai_curation/runtime/packages/<package>/` with:

- package-local Python code in `python/src/<package_name>/tools/`,
- a `tools/bindings.yaml` export that declares each `tool_id`, and
- package-owned agent bundles that reference those tool IDs.

Example layout:

```text
~/.agr_ai_curation/runtime/packages/org_custom/
├── package.yaml
├── requirements/runtime.txt
├── python/src/org_custom/tools/my_internal_api.py
└── tools/bindings.yaml
```

Minimal tool example:

```python
from agents import function_tool


@function_tool(
    name_override="my_internal_api",
    description_override="Query an internal API",
)
async def my_internal_api(query: str) -> dict:
    return {"results": [], "total": 0}
```

Declare that callable in the same package's `tools/bindings.yaml`, or use
[../README.md](../README.md) as the fuller package-first authoring guide.

If you are maintaining shipped tool catalogs from this repository, update the
package-owned sources in `packages/alliance/` for `agr.alliance` (Alliance
Defaults). `agr.core` (Alliance Core) intentionally ships no package-owned tool
bindings, so `backend/tools/custom/` is not the supported public extension
point for either shipped package.

## See also

- [../README.md](../README.md) - Package-first tool authoring
- [../../../config/agents/README.md](../../../config/agents/README.md) - Package-owned agent bundles
