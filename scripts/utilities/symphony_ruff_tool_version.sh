#!/usr/bin/env bash

# Shared pinned version/checksums for the Symphony VM ruff install.
# Override with environment variables if a newer release needs to be tested.

: "${SYMPHONY_RUFF_VERSION:=0.15.10}"

: "${SYMPHONY_RUFF_SHA256_LINUX_AMD64:=e3e9e5c791542f00d95edc74a506e1ac24efc0af9574de01ab338187bf1ff9f6}"
: "${SYMPHONY_RUFF_SHA256_LINUX_ARM64:=b775a5a09484549ac3fd377b5ce34955cf633165169671d1c4a215c113ce15df}"
