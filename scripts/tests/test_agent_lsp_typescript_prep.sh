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
mkdir -p node_modules/typescript/lib
printf 'ready\n' > node_modules/typescript/lib/tsserver.js
EOF
chmod +x "${temp_dir}/bin/typescript-language-server" "${temp_dir}/bin/npm"

PATH="${temp_dir}/bin:${PATH}" HOME="${temp_dir}" python3 - "${SCRIPT_PATH}" "${temp_dir}/workspace" <<'PY'
import importlib.util
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
PY

[[ "$(wc -l < "${temp_dir}/npm.log")" == "1" ]]
echo "agent_lsp TypeScript preparation tests passed"
