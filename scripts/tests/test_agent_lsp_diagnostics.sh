#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/agent_lsp.py"

assert_json() {
  local file="$1"
  local scenario="$2"
  python3 - "${file}" "${scenario}" <<'PY'
import json
import sys

path, scenario = sys.argv[1:3]
with open(path, encoding="utf-8") as handle:
    data = json.load(handle)

pyright = next(command for command in data["commands"] if command["name"] == "pyright")

if scenario == "missing_only":
    assert data["status"] == "ok", data
    assert pyright["returncode"] == 0, pyright
    assert pyright["raw_returncode"] == 1, pyright
    assert pyright["dependency_resolution_noise_count"] == 1, pyright
    assert pyright["actionable_diagnostic_count"] == 0, pyright
    assert "Dependency-resolution diagnostics classified as baseline noise: 1" in pyright["stdout"]
elif scenario == "mixed":
    assert data["status"] == "failed", data
    assert pyright["returncode"] == 1, pyright
    assert pyright["raw_returncode"] == 1, pyright
    assert pyright["dependency_resolution_noise_count"] == 1, pyright
    assert pyright["actionable_diagnostic_count"] == 1, pyright
    assert '"response" is possibly unbound' in pyright["stdout"]
    assert 'Import "sqlalchemy" could not be resolved' not in pyright["stdout"]
else:
    raise AssertionError(f"unknown scenario: {scenario}")
PY
}

test_pyright_dependency_noise_is_non_blocking() {
  local temp_dir output
  temp_dir="$(mktemp -d)"
  mkdir -p "${temp_dir}/bin"
  printf 'import sqlalchemy\n' > "${temp_dir}/missing_only.py"

  cat > "${temp_dir}/bin/ruff" <<'EOF'
#!/usr/bin/env bash
echo "All checks passed!"
exit 0
EOF
  cat > "${temp_dir}/bin/pyright" <<'EOF'
#!/usr/bin/env bash
cat <<JSON
{
  "version": "1.1.409",
  "generalDiagnostics": [
    {
      "file": "${PWD}/missing_only.py",
      "severity": "error",
      "message": "Import \"sqlalchemy\" could not be resolved",
      "range": {
        "start": { "line": 0, "character": 7 },
        "end": { "line": 0, "character": 17 }
      },
      "rule": "reportMissingImports"
    }
  ],
  "summary": {
    "filesAnalyzed": 1,
    "errorCount": 1,
    "warningCount": 0,
    "informationCount": 0
  }
}
JSON
exit 1
EOF
  chmod +x "${temp_dir}/bin/ruff" "${temp_dir}/bin/pyright"

  output="${temp_dir}/output.json"
  PATH="${temp_dir}/bin:${PATH}" python3 "${SCRIPT_PATH}" --root "${temp_dir}" diagnostics missing_only.py > "${output}"
  assert_json "${output}" "missing_only"

  echo "  PASS: test_pyright_dependency_noise_is_non_blocking"
  rm -rf "${temp_dir}"
}

test_pyright_actionable_diagnostics_still_fail() {
  local temp_dir output
  temp_dir="$(mktemp -d)"
  mkdir -p "${temp_dir}/bin"
  printf 'import sqlalchemy\nprint(response)\n' > "${temp_dir}/mixed.py"

  cat > "${temp_dir}/bin/ruff" <<'EOF'
#!/usr/bin/env bash
echo "All checks passed!"
exit 0
EOF
  cat > "${temp_dir}/bin/pyright" <<'EOF'
#!/usr/bin/env bash
cat <<JSON
{
  "version": "1.1.409",
  "generalDiagnostics": [
    {
      "file": "${PWD}/mixed.py",
      "severity": "error",
      "message": "Import \"sqlalchemy\" could not be resolved",
      "range": {
        "start": { "line": 0, "character": 7 },
        "end": { "line": 0, "character": 17 }
      },
      "rule": "reportMissingImports"
    },
    {
      "file": "${PWD}/mixed.py",
      "severity": "error",
      "message": "\"response\" is possibly unbound",
      "range": {
        "start": { "line": 1, "character": 6 },
        "end": { "line": 1, "character": 14 }
      },
      "rule": "reportPossiblyUnboundVariable"
    }
  ],
  "summary": {
    "filesAnalyzed": 1,
    "errorCount": 2,
    "warningCount": 0,
    "informationCount": 0
  }
}
JSON
exit 1
EOF
  chmod +x "${temp_dir}/bin/ruff" "${temp_dir}/bin/pyright"

  output="${temp_dir}/output.json"
  PATH="${temp_dir}/bin:${PATH}" python3 "${SCRIPT_PATH}" --root "${temp_dir}" diagnostics mixed.py > "${output}"
  assert_json "${output}" "mixed"

  echo "  PASS: test_pyright_actionable_diagnostics_still_fail"
  rm -rf "${temp_dir}"
}

echo "Running agent_lsp diagnostics tests..."
test_pyright_dependency_noise_is_non_blocking
test_pyright_actionable_diagnostics_still_fail
echo "agent_lsp diagnostics tests passed (2/2)"
