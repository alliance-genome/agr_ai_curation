#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/symphony_ready_for_pr.sh"

assert_contains() {
  local expected="$1"
  local actual="$2"
  if [[ "${actual}" != *"${expected}"* ]]; then
    echo "Expected output to contain '${expected}'" >&2
    printf 'Actual output:\n%s\n' "${actual}" >&2
    exit 1
  fi
}

test_no_pr_skips_lane() {
  local output
  output="$(
    bash "${SCRIPT_PATH}" \
      --delivery-mode no_pr \
      --issue-identifier ALL-46 \
      --branch all-46-no-pr
  )"

  assert_contains "READY_FOR_PR_STATUS=skip_no_pr" "${output}"
  assert_contains "READY_FOR_PR_NEXT_STATE=Human Review Prep" "${output}"
}

test_existing_pr_is_reported() {
  local temp_dir pr_json output
  temp_dir="$(mktemp -d)"
  pr_json="${temp_dir}/prs.json"

  cat > "${pr_json}" <<'EOF'
[{"number":42,"title":"ALL-42: Existing PR","url":"https://example.test/pr/42","headRefName":"all-42-branch"}]
EOF

  output="$(
    bash "${SCRIPT_PATH}" \
      --delivery-mode pr \
      --issue-identifier ALL-42 \
      --branch all-42-branch \
      --pr-json-file "${pr_json}"
  )"

  assert_contains "READY_FOR_PR_STATUS=existing_pr" "${output}"
  assert_contains "READY_FOR_PR_PR_NUMBER=42" "${output}"
}

test_missing_pr_reports_nonzero() {
  local temp_dir pr_json output_file rc output
  temp_dir="$(mktemp -d)"
  pr_json="${temp_dir}/prs.json"
  output_file="${temp_dir}/out.txt"

  echo '[]' > "${pr_json}"

  set +e
  bash "${SCRIPT_PATH}" \
    --delivery-mode pr \
    --issue-identifier ALL-50 \
    --branch all-50-branch \
    --pr-json-file "${pr_json}" \
    > "${output_file}"
  rc=$?
  set -e

  output="$(cat "${output_file}")"

  [[ "${rc}" == "20" ]] || {
    echo "Expected exit code 20, got ${rc}" >&2
    exit 1
  }

  assert_contains "READY_FOR_PR_STATUS=missing_pr" "${output}"
}

test_dry_run_create_reports_title() {
  local temp_dir pr_json output
  temp_dir="$(mktemp -d)"
  pr_json="${temp_dir}/prs.json"

  echo '[]' > "${pr_json}"

  output="$(
    bash "${SCRIPT_PATH}" \
      --delivery-mode pr \
      --issue-identifier ALL-51 \
      --branch all-51-branch \
      --repo alliance-genome/agr_ai_curation \
      --create-if-missing \
      --title "ALL-51: Example" \
      --pr-json-file "${pr_json}" \
      --dry-run
  )"

  assert_contains "READY_FOR_PR_STATUS=dry_run_create" "${output}"
  assert_contains "READY_FOR_PR_PR_TITLE=ALL-51: Example" "${output}"
}

test_no_pr_skips_lane
test_existing_pr_is_reported
test_missing_pr_reports_nonzero
test_dry_run_create_reports_title

echo "symphony_ready_for_pr tests passed"
