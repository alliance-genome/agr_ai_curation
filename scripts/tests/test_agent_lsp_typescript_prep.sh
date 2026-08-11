#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/agent_lsp.py"
temp_dir="$(mktemp -d)"
trap 'rm -rf "${temp_dir}"' EXIT

mkdir -p "${temp_dir}/workspace/frontend" "${temp_dir}/bin" "${temp_dir}/cache"
printf '{}\n' > "${temp_dir}/workspace/frontend/package-lock.json"
printf '{}\n' > "${temp_dir}/workspace/frontend/tsconfig.json"
printf 'let value = 1\n' > "${temp_dir}/workspace/frontend/example.ts"

cat > "${temp_dir}/bin/typescript-language-server" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat > "${temp_dir}/bin/npm" <<EOF
#!/usr/bin/env bash
printf 'npm ci\n' >> "${temp_dir}/npm.log"
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
first = module.ensure_typescript_dependencies(root)
second = module.ensure_typescript_dependencies(root)
assert first["status"] == "ready", first
assert first["prepared"] is True, first
assert second["status"] == "ready", second

shutil.rmtree(root / "frontend" / "node_modules")
Path(first["marker"]).unlink()
warm = module.warm_workspace(root, timeout=5)
assert warm["language_status"]["typescript"]["status"] == "ready", warm
assert (root / "frontend" / "node_modules" / "typescript" / "lib" / "tsserver.js").is_file()

warm_again = module.warm_workspace(root, timeout=5)
assert warm_again["language_status"]["typescript"]["status"] == "ready", warm_again

lockfile = root / "frontend" / "package-lock.json"
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
assert warm_unavailable["language_status"]["typescript"] == {
    "status": "unavailable",
    "reason": "language_server_missing",
}, warm_unavailable
assert Path(warm_unavailable["cache_dir"], "state.json").is_file(), warm_unavailable

shutil.rmtree(root / "frontend" / "node_modules")
Path(first["marker"]).unlink()
os.environ["FAIL_NPM_CI"] = "1"
try:
    module.warm_workspace(root, timeout=5)
except RuntimeError as exc:
    assert "fixture npm failure" in str(exc), exc
else:
    raise AssertionError("failed TypeScript preparation must fail warm loudly")
PY

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
# One install each for direct prep, cold warm, stale-marker warm, and the two
# intentional failure cases. The repeated ready-state calls must add none.
[[ "$(wc -l < "${temp_dir}/npm.log")" == "5" ]]
echo "agent_lsp TypeScript preparation tests passed"
