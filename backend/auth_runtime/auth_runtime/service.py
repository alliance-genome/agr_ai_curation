"""Canonical identity headers for authenticated internal-service requests."""

# AGR-branded trusted-caller headers were dropped after ALL-908 because core
# service authentication is project-neutral. Producers and consumers share these
# names; retired headers must never be read as an alternate shape.
TRUSTED_CALLER_SUB_HEADER = "X-Trusted-Caller-Sub"
TRUSTED_CALLER_EMAIL_HEADER = "X-Trusted-Caller-Email"
