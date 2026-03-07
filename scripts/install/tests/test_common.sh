#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
# shellcheck source=scripts/install/lib/common.sh
source "${repo_root}/scripts/install/lib/common.sh"

export NO_COLOR=1

require_non_empty "TEST_KEY" "value"
if require_non_empty "TEST_KEY" ""; then
  echo "require_non_empty should fail for empty value" >&2
  exit 1
fi

require_file_exists "${repo_root}/scripts/install/lib/common.sh"
if require_file_exists "${repo_root}/scripts/install/lib/missing.sh"; then
  echo "require_file_exists should fail for missing file" >&2
  exit 1
fi

require_command "bash"
if require_command "definitely-not-a-real-command"; then
  echo "require_command should fail for missing command" >&2
  exit 1
fi

valid_hex="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
require_hex_64 "ENCRYPTION_KEY" "$valid_hex"
if require_hex_64 "ENCRYPTION_KEY" "1234"; then
  echo "require_hex_64 should fail for invalid hex" >&2
  exit 1
fi

if ! prompt_yes_no "Continue" "yes" <<< "y"; then
  echo "prompt_yes_no should accept yes input" >&2
  exit 1
fi

if prompt_yes_no "Continue" "yes" <<< "n"; then
  echo "prompt_yes_no should reject no input" >&2
  exit 1
fi

if ! prompt_yes_no "Continue" "yes" <<< ""; then
  echo "prompt_yes_no should use yes default" >&2
  exit 1
fi

prompt_output="$(prompt_required_value "Enter value" <<< "provided")"
if [[ "$prompt_output" != "provided" ]]; then
  echo "prompt_required_value did not return expected value" >&2
  exit 1
fi

echo "common.sh checks passed"
