#!/usr/bin/env bash

# Shared pinned versions/checksums for the Symphony VM git safety scanners.
# Override with environment variables if a newer release needs to be tested.

: "${SYMPHONY_GITLEAKS_VERSION:=v8.30.1}"
: "${SYMPHONY_TRUFFLEHOG_VERSION:=v3.94.3}"

: "${SYMPHONY_GITLEAKS_SHA256_LINUX_AMD64:=551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb}"
: "${SYMPHONY_GITLEAKS_SHA256_LINUX_ARM64:=e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080}"

: "${SYMPHONY_TRUFFLEHOG_SHA256_LINUX_AMD64:=7cc45010bfac7258a23731bc3ab4371abdbf20ffc705075066971e5aa8ebda7f}"
: "${SYMPHONY_TRUFFLEHOG_SHA256_LINUX_ARM64:=57699423c593b63d5baa690ca4105f13b61c80480177160c6fc4b31cbac3af56}"
