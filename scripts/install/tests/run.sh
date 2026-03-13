#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bash "${script_dir}/test_templates.sh"
bash "${script_dir}/test_common.sh"
bash "${script_dir}/test_preflight.sh"
bash "${script_dir}/test_core_auth_group_stages.sh"
bash "${script_dir}/test_pdfx_stage.sh"
bash "${script_dir}/test_start_verify_stage.sh"
bash "${script_dir}/test_repo_migration.sh"

echo "All installer checks passed"
