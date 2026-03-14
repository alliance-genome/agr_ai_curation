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

If you are maintaining the shipped core package from this repository, update the
package-owned sources in `packages/core/` instead of treating
`backend/tools/custom/` as the supported public extension point.

## See also

- [../README.md](../README.md) - Package-first tool authoring
- [../../../config/agents/README.md](../../../config/agents/README.md) - Package-owned agent bundles
