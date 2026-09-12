#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/agent_lsp.py"
temp_dir="$(mktemp -d)"
trap 'rm -rf "${temp_dir}"' EXIT

mkdir -p \
  "${temp_dir}/workspace/frontend" \
  "${temp_dir}/workspace/agent_tests/midscene/src" \
  "${temp_dir}/workspace/docs/prototype" \
  "${temp_dir}/bin" \
  "${temp_dir}/cache"
printf '{}\n' > "${temp_dir}/workspace/frontend/package-lock.json"
printf '{"scripts":{"type-check:changed":"true"}}\n' > "${temp_dir}/workspace/frontend/package.json"
printf '{}\n' > "${temp_dir}/workspace/frontend/tsconfig.json"
printf 'let value = 1\n' > "${temp_dir}/workspace/frontend/example.ts"
printf '{}\n' > "${temp_dir}/workspace/agent_tests/midscene/package-lock.json"
printf '{"scripts":{"typecheck":"true"}}\n' > "${temp_dir}/workspace/agent_tests/midscene/package.json"
printf '{}\n' > "${temp_dir}/workspace/agent_tests/midscene/tsconfig.json"
printf 'export const nestedValue = 2\n' > "${temp_dir}/workspace/agent_tests/midscene/src/example.ts"
printf 'console.log("prototype")\n' > "${temp_dir}/workspace/docs/prototype/app.js"

cat > "${temp_dir}/bin/typescript-language-server" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat > "${temp_dir}/bin/npm" <<EOF
#!/usr/bin/env bash
printf '%s|npm %s\n' "\${PWD}" "\$*" >> "${temp_dir}/npm.log"
if [[ "\${1:-}" != "ci" ]]; then
  exit 0
fi
if [[ "\${FAIL_NPM_CI:-0}" == "1" ]]; then
  printf 'fixture npm failure\n' >&2
  exit 1
fi
mkdir -p node_modules/typescript/lib
printf 'ready\n' > node_modules/typescript/lib/tsserver.js
EOF
chmod +x "${temp_dir}/bin/typescript-language-server" "${temp_dir}/bin/npm"

PATH="${temp_dir}/bin:${PATH}" HOME="${temp_dir}" python3 - "${SCRIPT_PATH}" "${temp_dir}/workspace" <<'PY'
import importlib.util
import os
import shutil
import sys
from pathlib import Path

script, workspace = sys.argv[1:]
spec = importlib.util.spec_from_file_location("agent_lsp", script)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)

root = Path(workspace)
frontend = root / "frontend"
nested = root / "agent_tests" / "midscene"
npm_log = root.parent / "npm.log"

def npm_ci_count(project: Path) -> int:
    prefix = f"{project.resolve()}|npm ci"
    return sum(
        line.startswith(prefix)
        for line in npm_log.read_text().splitlines()
    )

first = module.ensure_typescript_dependencies(root, frontend)
frontend_ci_after_first = npm_ci_count(frontend)
second = module.ensure_typescript_dependencies(root, frontend)
assert first["status"] == "ready", first
assert first["prepared"] is True, first
assert second["status"] == "ready", second
assert "prepared" not in second, second
assert npm_ci_count(frontend) == frontend_ci_after_first
nested_first = module.ensure_typescript_dependencies(root, nested)
assert nested_first["status"] == "ready", nested_first
assert nested_first["project_root"] == "agent_tests/midscene", nested_first
assert module.typescript_projects(root) == [nested.resolve(), frontend.resolve()]
assert module.lsp_root_for(root, frontend / "example.ts") == frontend.resolve()
assert module.lsp_root_for(root, nested / "src" / "example.ts") == nested.resolve()

shutil.rmtree(frontend / "node_modules")
Path(first["marker"]).unlink()
warm = module.warm_workspace(root, timeout=5)
assert warm["language_status"]["typescript"]["status"] == "ready", warm
projects = warm["language_status"]["typescript"]["projects"]
assert [item["project_root"] for item in projects] == ["agent_tests/midscene", "frontend"], projects
assert "agent_tests/midscene/package-lock.json" in warm["fingerprint"]["config_hashes"], warm
assert "frontend/tsconfig.json" in warm["fingerprint"]["config_hashes"], warm
assert (frontend / "node_modules" / "typescript" / "lib" / "tsserver.js").is_file()

warm_again = module.warm_workspace(root, timeout=5)
assert warm_again["language_status"]["typescript"]["status"] == "ready", warm_again
assert all(
    "prepared" not in item
    for item in warm_again["language_status"]["typescript"]["projects"]
), warm_again

lockfile = frontend / "package-lock.json"
lockfile.write_text('{"lockfileVersion": 3}\n')
warm_stale_marker = module.warm_workspace(root, timeout=5)
assert warm_stale_marker["language_status"]["typescript"]["status"] == "ready", warm_stale_marker

real_which = module.shutil.which
module.shutil.which = lambda command: (
    None if command == "typescript-language-server" else real_which(command)
)
warm_unavailable = module.warm_workspace(root, timeout=5)
module.shutil.which = real_which
assert warm_unavailable["status"] == "partial", warm_unavailable
assert warm_unavailable["language_status"]["typescript"]["status"] == "unavailable", warm_unavailable
assert all(
    item["reason"] == "language_server_missing"
    for item in warm_unavailable["language_status"]["typescript"]["projects"]
), warm_unavailable
assert Path(warm_unavailable["cache_dir"], "state.json").is_file(), warm_unavailable

shutil.rmtree(frontend / "node_modules")
Path(first["marker"]).unlink()
os.environ["FAIL_NPM_CI"] = "1"
try:
    module.warm_workspace(root, timeout=5)
except RuntimeError as exc:
    assert "fixture npm failure" in str(exc), exc
else:
    raise AssertionError("failed TypeScript preparation must fail warm loudly")

os.environ.pop("FAIL_NPM_CI")
module.ensure_typescript_dependencies(root, frontend)

class FakeLspClient:
    instances = []

    def __init__(self, command, cwd):
        self.command = command
        self.cwd = cwd
        self.requests = []
        self.notifications = []
        self.__class__.instances.append(self)

    def request(self, method, params, timeout):
        self.requests.append((method, params))
        if method == "textDocument/documentSymbol":
            return [{"name": "nestedValue", "kind": 13, "range": {"start": {"line": 0, "character": 13}}}]
        if method == "textDocument/references":
            return [{"uri": params["textDocument"]["uri"], "range": {"start": {"line": 0, "character": 13}}}]
        return {"capabilities": {}}

    def notify(self, method, params):
        self.notifications.append((method, params))

    def close(self):
        pass

module.LspClient = FakeLspClient
module.time.sleep = lambda _seconds: None
semantic_projects = [
    (nested, nested / "src" / "example.ts"),
    (frontend, frontend / "example.ts"),
]
for project, path in semantic_projects:
    symbols = module.lsp_document_request(
        root=root,
        path=path,
        method="textDocument/documentSymbol",
        params={"textDocument": {"uri": module.file_uri(path)}},
        timeout=5,
    )
    references = module.lsp_document_request(
        root=root,
        path=path,
        method="textDocument/references",
        params={
            "textDocument": {"uri": module.file_uri(path)},
            "position": {"line": 0, "character": 13},
        },
        timeout=5,
    )
    assert symbols[0]["name"] == "nestedValue", symbols
    assert references[0]["uri"] == module.file_uri(path), references

for client, (project, _path) in zip(
    FakeLspClient.instances,
    [semantic_projects[0], semantic_projects[0], semantic_projects[1], semantic_projects[1]],
    strict=True,
):
    assert client.cwd == project.resolve(), client.cwd
    initialize = next(
        params for method, params in client.requests if method == "initialize"
    )
    assert initialize["initializationOptions"]["tsserver"]["path"] == str(
        project / "node_modules" / "typescript" / "lib" / "tsserver.js"
    ), initialize

diagnostics = module.run_diagnostics(
    root,
    ["frontend/example.ts", "agent_tests/midscene/src/example.ts"],
    timeout=5,
)
assert diagnostics["status"] == "ok", diagnostics
assert [command["name"] for command in diagnostics["commands"]] == [
    "agent_tests/midscene typecheck",
    "frontend type-check:changed",
], diagnostics
unowned_diagnostics = module.run_diagnostics(
    root,
    ["docs/prototype/app.js", "agent_tests/midscene/src/example.ts"],
    timeout=5,
)
assert unowned_diagnostics["status"] == "failed", unowned_diagnostics
assert [command["name"] for command in unowned_diagnostics["commands"]] == [
    "typescript project unresolved",
    "agent_tests/midscene typecheck",
], unowned_diagnostics
assert unowned_diagnostics["commands"][0]["files"] == ["docs/prototype/app.js"]
assert "No TypeScript project contains" in unowned_diagnostics["commands"][0]["stderr"]
assert npm_ci_count(frontend) == frontend_ci_after_first + 4
assert npm_ci_count(nested) == 1
PY

rm -rf "${temp_dir}/workspace/frontend/node_modules"
set +e
cli_output="$(
  FAIL_NPM_CI=1 PATH="${temp_dir}/bin:${PATH}" HOME="${temp_dir}" \
    "${SCRIPT_PATH}" --root "${temp_dir}/workspace" warm 2>&1
)"
cli_status=$?
set -e

[[ "${cli_status}" == "2" ]]
printf '%s\n' "${cli_output}" | jq -e \
  '.status == "error" and (.error | contains("fixture npm failure"))' >/dev/null
# Each nested project is prepared independently; assertions above verify cache
# hits, project-specific semantic roots, explicit tsserver paths, and diagnostics.
grep -q 'workspace/agent_tests/midscene|npm ci' "${temp_dir}/npm.log"
grep -q 'workspace/frontend|npm ci' "${temp_dir}/npm.log"
grep -q 'workspace/agent_tests/midscene|npm run typecheck' "${temp_dir}/npm.log"
grep -q 'workspace/frontend|npm run type-check:changed' "${temp_dir}/npm.log"
echo "agent_lsp TypeScript preparation tests passed"
