#!/usr/bin/env bash

assert_contains() {
  local pattern="$1"
  local file="$2"
  if ! rg -n --fixed-strings "$pattern" "$file" >/dev/null 2>&1; then
    echo "Expected to find '$pattern' in $file" >&2
    exit 1
  fi
}

assert_not_contains() {
  local pattern="$1"
  local file="$2"
  if rg -n --fixed-strings "$pattern" "$file" >/dev/null 2>&1; then
    echo "Did not expect to find '$pattern' in $file" >&2
    exit 1
  fi
}

assert_count() {
  local expected="$1"
  local pattern="$2"
  local file="$3"
  local actual

  actual="$(rg -c --fixed-strings "$pattern" "$file" || true)"
  if [[ "${actual}" != "${expected}" ]]; then
    echo "Expected ${expected} matches for '${pattern}' in ${file}, got ${actual}" >&2
    exit 1
  fi
}
